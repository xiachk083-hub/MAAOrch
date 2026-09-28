# -*- coding: utf-8 -*-
"""Hermes 调度器 -> AUTO-MAS 发车（替代 AUTO-MAS 自带定时）。

背景（2026-09-27 起）：AUTO-MAS 的 24 条定时（日常定时1-12 @05:00..05:33、晚班定时1-12 @19:30..20:03）
不再由 AUTO-MAS 触发，改由 Hermes 的 cron 触发本脚本发车：

    POST /api/dispatch/start {"taskId": "<队列uid>", "mode": "AutoProxy"}

用法：
  python tools/mas_dispatch.py --list                     只读：24 条班次队列 + 定时项 + 今日进度 + 告警数
  python tools/mas_dispatch.py --check                    自检（白名单/闸门/连通/状态文件/告警台账/锁），退出码 0/2
  python tools/mas_dispatch.py --reconcile                对账（dry-run）：到点且今天没发过的队列会发什么
  python tools/mas_dispatch.py --reconcile --apply        真发车（Hermes cron 用这个）
  python tools/mas_dispatch.py --shift daily --apply      整班发车（默认按定时表 3 分钟错峰）
  python tools/mas_dispatch.py --queue 日常定时1 --apply   单条发车
  python tools/mas_dispatch.py --alerts                   只读：当前未消除的发车告警台账
  python tools/mas_dispatch.py --clear-test-alerts        清掉 --task-id 自测留下的 test 告警
  python tools/mas_dispatch.py --selftest                 离线自测（假传输，不碰网络、不碰真 runtime）
  python tools/mas_dispatch.py --check-schedule           只读：SHIFT_TABLE ↔ AUTO-MAS 队列/时间/槽位一致性校验
  python tools/mas_dispatch.py --busy                     只读：当前 busy 窗口（HOLD_MIN）+ 未关闭 late 标记
  python tools/mas_dispatch.py --release 日常定时3 --apply  人工确认对方已结束后清 busy 窗口（解锁同资源补发）
  python tools/mas_dispatch.py --dismiss-late 日常定时3 --apply [--note 理由]  关闭 late 标记（决定不补）
  任何命令都可加 --json 输出机器可读结果。

闸门（真发车前必须全过，任一不过即拒绝且零上游写请求）：
  1) 队列名白名单：^(日常|晚班)定时([1-9]|1[0-2])$
  2) AUTO-MAS 可达（POST /api/scripts/get 探测）；不可达 => fail-closed 拒绝
  3) 单飞：同一队列同一班次当天只发一次（状态文件）；重复须 --force
  4) MAAORCH_DISPATCH_NO_GO=1 硬闸 => 拒绝一切真发车
  5) 返回体必须 code==200 且带 taskId，否则记为 failed
  6) 并发锁 runtime/mas_dispatch.lock：真发车临界区串行（含重试等待）；锁被占 => 本轮跳过，下一跳重试

失败分类与重试（2026-09-27 起「发车失败不再静默」）：
  类别（reason）：rest_unreachable / http_error / bad_response / no_taskid /
                  target_running / slot_occupied / target_not_found / queue_missing / unknown_error
  可重试类（REST 抖动、目标已在运行、槽位被占）做有上限的退避重试：
      默认 3 次，等待 60/180/300 秒（RETRIES/RETRY_DELAYS 可调；dry-run 不等待）。
  重试预算：单进程退避总预算 RETRY_BUDGET_S（默认 700s）——超出则记 deferred、
      不入台账、留给下一跳（cron 每 3 分钟一跳，追发窗口内自愈）。
  终态失败：
      · 写 runtime/mas_dispatch_log.jsonl（每次尝试一行，含 reason/attempt/terminal）；
      · 记入 runtime/mas_dispatch_alerts.json 台账（active）；
      · stdout 输出一行 DISPATCH_ALERT=<队列:原因>（cron/巡检据此显式告警）。
  自愈：失败队列下一跳发车成功 => 台账自动消除，stdout 输出 DISPATCH_ALERT_CLEARED=<队列>。
  自测：--task-id 注入故意坏 id（test 标记，巡检视图排除），--clear-test-alerts 回收。

并发冲突与补发边界（2026-09-27 起「发车不再双占资源」）：
  一致性校验：--check-schedule 对比 SHIFT_TABLE 与 AUTO-MAS 实际队列/定时项/队列项，
      报告 缺失队列 / 表外定时队列 / 时间与开关漂移 / 队列为空 / 项指向不存在脚本 /
      槽位对(日常N 与 晚班N)脚本集不一致 / 脚本跨槽重复；FAIL 退出码 2（只读，不改上游）。
  冲突闸门：真发车前按「MAA 目录 + 脚本 sid + 实例号」比对同窗（HOLD_MIN，默认 150 分钟）
      内已发车队列；资源重叠 => 拒绝（refused=conflict_inflight）并写台账，不打上游 POST。
      旧条目无 resources 时退化为槽位启发式（同槽位对保守拒绝，标 unknown_resources）。
      显式放行：--force-conflict（记 conflict_overridden，仅当确认对方任务已结束时用）。
      人工清理：--release <队列> [--apply] 清掉 busy 窗口（对方任务确已结束时用）。
  补发边界：--reconcile 超窗（>GRACE 分钟）只记 late 标记 + 台账告警（late_handoff），
      不发车、不自动补；补发通道 = 巡检/人工 --queue <队列> --apply（成功即自动关标记+清告警），
      或 --dismiss-late <队列> --apply [--note 理由] 明确放弃（告警按 dismissed 清除）。
  状态自愈：state 损坏 => 隔离到 <state>.corrupt-<ts>（留最近 5 份，审计记 state_quarantine），
      本轮按空状态继续；非 dict 条目剔除，未知顶层键保留。

环境变量：
  MAAORCH_MAS_API        默认 http://100.79.173.69:36163
  MAAORCH_DISPATCH_NO_GO 1/true/yes/on = 禁发车
  MAAORCH_DISPATCH_GRACE 对账追发窗口（分钟，默认 30；超出只告警不发车）
  MAAORCH_DISPATCH_RETRIES        重试次数（默认 3；0=不重试）
  MAAORCH_DISPATCH_RETRY_DELAYS   退避秒表（默认 "60,180,300"）
  MAAORCH_DISPATCH_RETRY_BUDGET_S 单进程退避总预算秒（默认 700）
  MAAORCH_DISPATCH_LOCK_STALE_S   并发锁陈旧阈值秒（默认 1500，超过可抢锁）
  MAAORCH_DISPATCH_HOLD_MIN       busy 窗口分钟（默认 150）：资源冲突同窗判定 + 人工 release 的依据
  MAAORCH_DISPATCH_RUNTIME        覆盖 runtime 目录（测试用；默认 <repo>/runtime）

状态/审计：
  runtime/mas_dispatch_state.json   当天每条队列的发放记录
  runtime/mas_dispatch_log.jsonl    每次尝试一行（ok/fail/deferred，含 reason/attempt/terminal/test）
  runtime/mas_dispatch_alerts.json  未消除告警台账 active + 已消除历史 cleared

回滚：本轮改动（重试/告警/锁/--json）之前的行为即
  tools/mas_dispatch.py.bak-retryalert-20260927-205715（改动前完整备份，直接覆盖回来即可；
  新增的 runtime/mas_dispatch_alerts.json 台账与日志互不依赖，可留可删）。
  加固附加（冲突/一致性/late/自愈）无独立回滚点：与上述同函数族，整体回滚同上；
  新增 state 字段（resources/busy_until/conflict_overridden/late）对旧代码透明。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("MAAORCH_DISPATCH_RUNTIME") or os.path.join(ROOT, "runtime")
STATE = os.path.join(RUNTIME, "mas_dispatch_state.json")
LOG = os.path.join(RUNTIME, "mas_dispatch_log.jsonl")
ALERTS = os.path.join(RUNTIME, "mas_dispatch_alerts.json")
LOCK = os.path.join(RUNTIME, "mas_dispatch.lock")
API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
QUEUE_RE = re.compile(r"^(日常|晚班)定时([1-9]|1[0-2])$")
NO_GO = str(os.environ.get("MAAORCH_DISPATCH_NO_GO", "")).strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


def _env_int(name: str, default: int) -> int:
    try:
        raw = str(os.environ.get(name, "")).strip()
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _parse_delays(raw, default=(60, 180, 300)) -> list:
    """解析 "60,180,300" 形式的退避秒表；任何一项不合法就整体回退默认值。"""
    if raw is None:
        return list(default)
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not parts:
        return list(default)
    out = []
    for p in parts:
        try:
            out.append(max(0, int(p)))
        except ValueError:
            return list(default)
    return out


GRACE_MIN = _env_int("MAAORCH_DISPATCH_GRACE", 30)
RETRIES = max(0, _env_int("MAAORCH_DISPATCH_RETRIES", 3))
RETRY_DELAYS = _parse_delays(os.environ.get("MAAORCH_DISPATCH_RETRY_DELAYS"))
RETRY_BUDGET_S = max(0, _env_int("MAAORCH_DISPATCH_RETRY_BUDGET_S", 700))
LOCK_STALE_S = max(60, _env_int("MAAORCH_DISPATCH_LOCK_STALE_S", 1500))
HOLD_MIN = max(1, _env_int("MAAORCH_DISPATCH_HOLD_MIN", 150))
_QUARANTINE_KEEP = 5
_PROC_START = time.time()


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def post(path: str, body: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(body if body is not None else {}).encode("utf-8")
    req = urllib.request.Request(API + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def reachable() -> tuple[bool, str]:
    try:
        r = post("/api/scripts/get", {}, timeout=15)
        return (r.get("code") == 200), "code=%s" % r.get("code")
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


def queue_map() -> dict:
    """{队列名: 队列uid}"""
    r = post("/api/queue/get", {"queueId": None})
    out = {}
    for uid, v in (r.get("data") or {}).items():
        name = ((v or {}).get("Info") or {}).get("Name")
        if name:
            out[name] = uid
    return out


SHIFT_TABLE = {
    "日常定时1": "05:00", "日常定时2": "05:03", "日常定时3": "05:06", "日常定时4": "05:09",
    "日常定时5": "05:12", "日常定时6": "05:15", "日常定时7": "05:18", "日常定时8": "05:21",
    "日常定时9": "05:24", "日常定时10": "05:27", "日常定时11": "05:30", "日常定时12": "05:33",
    "晚班定时1": "19:30", "晚班定时2": "19:33", "晚班定时3": "19:36", "晚班定时4": "19:39",
    "晚班定时5": "19:42", "晚班定时6": "19:45", "晚班定时7": "19:48", "晚班定时8": "19:51",
    "晚班定时9": "19:54", "晚班定时10": "19:57", "晚班定时11": "20:00", "晚班定时12": "20:03",
}
"""班次表 = Hermes 自己持有（2026-09-27 从 AUTO-MAS 的定时项逐条抄下来后接管）。
换班/改点只改这里；AUTO-MAS 侧的定时项已 Enabled=false，仅作回滚备份。"""


def mas_timers() -> dict:
    """{队列名: {'Enabled': bool, 'Time': 'HH:MM'}} —— 只用于漂移比对，不参与决策。"""
    out = {}
    for name, uid in queue_map().items():
        if not QUEUE_RE.match(name):
            continue
        try:
            t = post("/api/queue/time/get", {"queueId": uid})
        except Exception:  # noqa: BLE001
            continue
        for _tsid, v in (t.get("data") or {}).items():
            info = (v or {}).get("Info") or {}
            if info.get("Time"):
                out[name] = {"Enabled": bool(info.get("Enabled")), "Time": str(info["Time"])}
    return out


def schedule() -> dict:
    """Hermes 的权威班次表（名称 -> HH:MM）。"""
    return dict(SHIFT_TABLE)


def shift_of(name: str) -> str:
    return "daily" if name.startswith("日常") else "evening"


def _quarantine_state(err: str) -> str:
    """损坏的 state 隔离到 <STATE>.corrupt-<ts>（留最近 5 份），返回隔离路径（失败空串）。"""
    import shutil
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = "%s.corrupt-%s" % (STATE, ts)
    try:
        os.replace(STATE, dst)  # 隔离=移走，后续按空状态自愈
    except OSError:
        try:
            shutil.copy2(STATE, dst)
        except OSError:
            return ""
    d = os.path.dirname(STATE) or "."
    try:
        files = sorted(f for f in os.listdir(d) if f.startswith(os.path.basename(STATE) + ".corrupt-"))
        for f in files[:-_QUARANTINE_KEEP]:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(d, f))
    except OSError:
        pass
    with contextlib.suppress(Exception):
        audit({"ts": _now(), "kind": "state_quarantine", "file": dst, "error": err[:300]})
    print("WARN mas_dispatch: 状态文件损坏（%s），已隔离到 %s，按空状态继续（下一跳重建）"
          % (err[:120], dst), file=sys.stderr)
    return dst


def load_state() -> dict:
    """读状态；损坏 => 隔离 + 自愈为空（不阻塞发车，下一跳重建）。"""
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001
        _quarantine_state("%s: %s" % (type(e).__name__, e))
        return {}
    if not isinstance(data, dict):
        _quarantine_state("top-level not dict: %s" % type(data).__name__)
        return {}
    fixed = {}
    for k, v in data.items():
        if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)):
            if isinstance(v, dict):
                fixed[k] = {q: r for q, r in v.items() if isinstance(r, dict)}
            continue
        if k in ("late", "meta"):
            if isinstance(v, dict):
                fixed[k] = v
            continue
        fixed[k] = v  # 未知键原样保留（兼容未来字段）
    return fixed


def save_state(st: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


# ---------------------------------------------------------------- 统一审计流（双写期）
# 接线卡 t_a190ca4b：旧流（mas_dispatch_log）照写（不动写出行为）；旁路再补一条
# tools/mas_audit.py 统一事件（schema maaorch.audit.v1）。本文件写的是**真动作权威行**，
# kind→action 映射与 tools/mas_audit.py::_ad_dispatch 同口径。失败绝不打断主流程。
_DISPATCH_ACTION = {          # 非 dispatch/stop/defer 家族的 kind（桥接器认得的四种）
    "late_handoff": ("dispatch.late_handoff", "refused"),
    "late_resolved": ("dispatch.late_resolved", "ok"),
    "late_dismissed": ("dispatch.late_dismissed", "ok"),
    "busy_release": ("dispatch.busy_release", "ok"),
    "state_quarantine": ("dispatch.state_quarantine", "info"),
}


def _unified_from_audit(rec: dict) -> None:
    """事件行（mas_dispatch_log）→ 统一流（映射同 mas_audit._ad_dispatch ①②③）。"""
    kind = str(rec.get("kind") or "").strip()
    ok = rec.get("ok")
    action = target = None
    if kind in _DISPATCH_ACTION:
        action, res = _DISPATCH_ACTION[kind]
    elif "refused" in kind:
        action, res = "dispatch.refuse", "refused"
    elif kind.startswith("stop"):
        action, res = "dispatch.stop", ("ok" if ok else "fail")
    elif kind.startswith("dispatch"):
        action, res = "dispatch.start", ("ok" if ok else "fail")
    elif kind.startswith("defer"):
        action, res = "dispatch.defer", "info"
    elif kind:
        action, res = "dispatch." + re.sub(r"[^a-z0-9_]+", "_", kind.lower()), (
            "ok" if ok is True else ("fail" if ok is False else "info"))
    else:
        # 无 kind 的旧形态（本机已不再产出；保留给外部写入者）：班次发车 / 手工发车
        if rec.get("queue"):
            action, res = "dispatch.start", ("ok" if ok else "fail")
        elif rec.get("script") or rec.get("dir"):
            action, res = "dispatch.start", ("ok" if (ok if ok is not None else rec.get("http") == 200)
                                             else "fail")
        else:
            return
    queue = str(rec.get("queue") or "").strip()
    if queue:
        target = "queue:" + queue
    else:
        tk = str(rec.get("target_kind") or "").strip()
        tv = str(rec.get("target") or rec.get("dir") or rec.get("script") or "").strip()
        if tv:
            target = ("%s:%s" % (tk, tv)) if tk and tk != "-" else ("dir:%s" % tv if rec.get("dir")
                                                                    else ("task:%s" % tv))
    target = target or "-"
    run_id = str(rec.get("taskId") or "")
    params = {k: rec.get(k) for k in ("mode", "queue_id", "apply", "force", "preflight", "resources",
                                      "detail", "attempts", "retries_used", "final", "test",
                                      "deferred", "by", "scheduled", "grace_min", "note",
                                      "alerts_cleared", "error", "refused", "source", "code",
                                      "attempt", "terminal")
              if rec.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor="tool:mas_dispatch", action=action or "dispatch." + kind, target=target, result=res,
        reason=str(rec.get("reason") or rec.get("detail") or "")[:300], params=params,
        evidence=[LOG], card=os.environ.get("HERMES_KANBAN_TASK", ""), run_id=run_id,
        path=mas_audit.stream_for_runtime(RUNTIME))


def audit(rec: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        _unified_from_audit(rec)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 失败分类（2026-09-27）
# ---------------------------------------------------------------------------

# 上游 AUTO-MAS 的 dispatch 错误形态是 code=500 + message（见 D:\MAS app/api/dispatch.py，
# 抛错点 app/core/task_manager.py:304/316/319）：
#   ValueError「任务 X 无法找到对应脚本配置」= 找不到目标（含坏 taskId）；
#   RuntimeError「任务 X 已在运行」= 目标忙；
#   队列项被别处锁定时是「…已被其他任务调度器锁定」（WS 警告文案）。
# 模式表按语义收录中英关键词；命中不到的归 bad_response（终态、要人看）。
_BUSY_RUNNING = ("已在运行", "正在运行", "运行中", "already running", "is running", "正在执行")
_BUSY_SLOT = ("占用", "被占", "槽位", "已被其他任务", "被其他任务", "调度器锁定", "已锁定", "被锁定", "锁定", "locked", "busy")
_NOT_FOUND = ("无法找到", "找不到", "不存在", "not found", "badly formed", "无效", "invalid")


def _hit(msg: str, pats) -> bool:
    low = msg.lower()
    return any((p in msg) or (p.lower() in low) for p in pats)


def _classify_exc(e: BaseException) -> tuple[str, str, bool]:
    """异常 -> (reason, detail, retryable)。"""
    if isinstance(e, urllib.error.HTTPError):
        code = int(getattr(e, "code", 0) or 0)
        retryable = code in (408, 425, 429) or 500 <= code < 600
        return "http_error", "HTTP %s %s" % (code, getattr(e, "reason", "")), retryable
    if isinstance(e, json.JSONDecodeError):
        return "bad_response", "应答不是合法 JSON: %s" % e, False
    if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return "rest_unreachable", "%s: %s" % (type(e).__name__, e), True
    return "unknown_error", "%s: %s" % (type(e).__name__, e), False


def _classify_resp(r) -> dict:
    """AUTO-MAS 应答 -> 结构化结果 {ok, reason?, retryable?, detail?, taskId?, resp}。"""
    if not isinstance(r, dict):
        return {"ok": False, "reason": "bad_response", "retryable": False,
                "detail": "应答不是 JSON 对象: %r" % (r,), "resp": r}
    code = r.get("code")
    msg = str(r.get("message") or "")
    if code == 200 and r.get("taskId"):
        return {"ok": True, "taskId": r.get("taskId"), "resp": r}
    if code == 200:
        return {"ok": False, "reason": "no_taskid", "retryable": False,
                "detail": "code=200 但无 taskId（message=%s）" % msg[:200], "resp": r}
    if _hit(msg, _BUSY_RUNNING):
        return {"ok": False, "reason": "target_running", "retryable": True,
                "detail": "code=%s %s" % (code, msg[:300]), "resp": r}
    if _hit(msg, _BUSY_SLOT):
        return {"ok": False, "reason": "slot_occupied", "retryable": True,
                "detail": "code=%s %s" % (code, msg[:300]), "resp": r}
    if _hit(msg, _NOT_FOUND):
        return {"ok": False, "reason": "target_not_found", "retryable": False,
                "detail": "code=%s %s" % (code, msg[:300]), "resp": r}
    return {"ok": False, "reason": "bad_response", "retryable": False,
            "detail": "code=%s %s" % (code, msg[:300]), "resp": r}


def _attempt(name: str, post_target: str, need_resolve: bool, timeout: int = 60) -> dict:
    """一次发车尝试：探活（fail-closed）-> 解析队列 uid（如需）-> POST dispatch/start。"""
    ok, why = reachable()
    if not ok:
        return {"ok": False, "reason": "rest_unreachable", "retryable": True, "detail": why}
    resolved = post_target
    if need_resolve and not resolved:
        try:
            qm = queue_map()
        except Exception as e:  # noqa: BLE001
            reason, detail, retryable = _classify_exc(e)
            return {"ok": False, "reason": reason, "retryable": retryable, "detail": detail}
        if name not in qm:
            return {"ok": False, "reason": "queue_missing", "retryable": False,
                    "detail": "队列名 %s 未在 AUTO-MAS 找到（改名/删除？）" % name}
        resolved = qm[name]
    try:
        r = post("/api/dispatch/start", {"taskId": resolved, "mode": "AutoProxy"}, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        reason, detail, retryable = _classify_exc(e)
        res = {"ok": False, "reason": reason, "retryable": retryable, "detail": detail}
        res["post_target"] = resolved
        return res
    res = _classify_resp(r)
    res["post_target"] = resolved
    return res


# ---------------------------------------------------------------------------
# 并发锁（真发车串行；重试等待期间持锁，防 cron / MCP / 手动三路双发）
# ---------------------------------------------------------------------------

class _LockBusy(RuntimeError):
    pass


@contextlib.contextmanager
def _dispatch_lock(enabled: bool = True, path: str | None = None):
    path = path or LOCK
    if not enabled:
        yield
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    created = False
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("pid=%d ts=%s\n" % (os.getpid(), _now()))
            created = True
            break
        except FileExistsError:
            holder = ""
            with contextlib.suppress(OSError):
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    holder = fh.read().strip()[:120]
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                age = 0.0
            if attempt == 1 and age > LOCK_STALE_S:
                # 陈旧锁（持有进程被 kill/超时未清）：抢过来继续
                with contextlib.suppress(OSError):
                    os.remove(path)
                continue
            raise _LockBusy("锁被占（%s，%.0fs 前）" % (holder or "未知持有者", age))
    try:
        yield
    finally:
        if created:
            with contextlib.suppress(OSError):
                os.remove(path)


# ---------------------------------------------------------------------------
# 告警台账（runtime/mas_dispatch_alerts.json）
#   active:  [{queue, date, reason, detail, ts, attempts, test}]
#   cleared: 已消除（成功自愈 cleared_by=dispatch_ok / 过期 expired），留最近 200 条
# ---------------------------------------------------------------------------

_ALERT_TTL_DAYS = 7


def _alerts_load() -> dict:
    try:
        with open(ALERTS, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("active"), list):
            d.setdefault("cleared", [])
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"active": [], "cleared": []}


def _alerts_save(d: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    d["updated"] = _now()
    tmp = ALERTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ALERTS)


def _alerts_prune(d: dict) -> None:
    cutoff = (dt.date.today() - dt.timedelta(days=_ALERT_TTL_DAYS)).isoformat()
    keep, expired = [], []
    for e in d.get("active") or []:
        if str(e.get("date") or "") < cutoff:
            expired.append(dict(e, cleared_at=_now(), cleared_by="expired"))
        else:
            keep.append(e)
    if expired:
        d["active"] = keep
        d["cleared"] = ((d.get("cleared") or []) + expired)[-200:]


def _alert_key(e: dict) -> tuple:
    return (e.get("queue"), e.get("date"), e.get("reason"), bool(e.get("test")))


def alert_fail(queue: str, reason: str, detail: str, attempts: int, test: bool = False) -> str:
    """记一条终态失败告警（同队列同天同原因去重，只刷新 attempts/时间）。返回 '<queue>:<reason>'。"""
    d = _alerts_load()
    today = dt.date.today().isoformat()
    key = (queue, today, reason, bool(test))
    for e in d["active"]:
        if _alert_key(e) == key:
            e["ts"] = _now()
            e["attempts"] = attempts
            e["detail"] = (detail or "")[:500]
            break
    else:
        d["active"].append({"queue": queue, "date": today, "reason": reason,
                            "detail": (detail or "")[:500], "ts": _now(),
                            "attempts": attempts, "test": bool(test)})
    _alerts_prune(d)
    _alerts_save(d)
    return "%s:%s" % (queue, reason)


def alert_clear(queue: str, test: bool = False, cleared_by: str = "dispatch_ok") -> list:
    """发车成功自愈：清掉该队列（同 test 属性）的全部未消除告警；返回被清的 '<queue>:<reason>'。"""
    d = _alerts_load()
    keep, cleared = [], []
    for e in d["active"]:
        if e.get("queue") == queue and bool(e.get("test")) == bool(test):
            cleared.append(dict(e, cleared_at=_now(), cleared_by=cleared_by))
        else:
            keep.append(e)
    if cleared:
        d["active"] = keep
        d["cleared"] = ((d.get("cleared") or []) + cleared)[-200:]
        _alerts_save(d)
    return ["%s:%s" % (e["queue"], e["reason"]) for e in cleared]


def active_alerts(exclude_test: bool = True) -> list:
    """未消除告警（默认排除 test 自测条目——巡检视图用）。"""
    d = _alerts_load()
    out = [dict(e) for e in d["active"] if not (exclude_test and e.get("test"))]
    out.sort(key=lambda e: (str(e.get("date") or ""), str(e.get("queue") or "")))
    return out


def alert_lines(exclude_test: bool = True) -> list:
    """['<queue>:<reason>', ...] —— 巡检里 DISPATCH_ALERT= 的值。"""
    return ["%s:%s" % (e.get("queue"), e.get("reason")) for e in active_alerts(exclude_test)]


def clear_test_alerts() -> int:
    """回收 --task-id 自测留下的 test 告警；返回清掉的条数。"""
    d = _alerts_load()
    keep, removed = [], 0
    for e in d["active"]:
        if e.get("test"):
            removed += 1
        else:
            keep.append(e)
    if removed:
        d["active"] = keep
        _alerts_save(d)
    return removed


# ---------------------------------------------------------------------------
# 一致性校验：SHIFT_TABLE ↔ AUTO-MAS 实际队列（名称/时间/项）——只读
#   暴露为 `--check-schedule`；差异分 problems（要处理）/warnings（只提示）。
# ---------------------------------------------------------------------------

_N_RE = re.compile(r"^(?:日常|晚班)定时(\d+)$")


def norm_dir(p) -> str:
    """目录归一：/ -> \\，折叠重复分隔符，去尾部，小写（比对用）。"""
    s = str(p or "").strip().replace("/", "\\")
    s = re.sub(r"\\+", "\\\\", s)
    return s.rstrip("\\").lower()


def snapshot_queues(names=None) -> dict:
    """{队列名: {uid, count, scripts:[{sid,name,dir,emu}], names:[...], bad_items:[...]}}

    names=None => 白名单匹配的全部定时队列；一次 scripts/get + 每队列一次 item/get。
    """
    scripts = (post("/api/scripts/get", {}).get("data") or {})
    smeta = {}
    for sid, v in scripts.items():
        info = (v or {}).get("Info") or {}
        emu = ((v or {}).get("Emulator") or {}).get("Index")
        smeta[str(sid)] = {"sid": str(sid), "name": str(info.get("Name") or ""),
                           "dir": norm_dir(info.get("Path")),
                           "emu": str(emu) if emu is not None else ""}
    out = {}
    for name, uid in queue_map().items():
        if names is not None:
            if name not in names:
                continue
        elif not QUEUE_RE.match(name):
            continue
        try:
            items = (post("/api/queue/item/get", {"queueId": uid}).get("data") or {})
        except Exception:  # noqa: BLE001
            items = None
        if items is None:
            out[name] = {"uid": uid, "count": None, "scripts": [], "names": [],
                         "error": "item_get_failed"}
            continue
        sids, bad = [], []
        for _iid, iv in items.items():
            sid = str(((iv or {}).get("Info") or {}).get("ScriptId") or "")
            if not sid:
                continue
            if sid in smeta:
                sids.append(sid)
            else:
                bad.append(sid)
        out[name] = {"uid": uid, "count": len(sids) + len(bad), "scripts": [smeta[s] for s in sids],
                     "names": [smeta[s]["name"] for s in sids], "bad_items": bad}
    return out


def check_schedule(snapshot: dict | None = None, timers: dict | None = None,
                   all_names: list | None = None) -> dict:
    """SHIFT_TABLE ↔ AUTO-MAS 差异检测。返回 {ok, problems, warnings, drift, brief}。

    检查项：
      1) 名称差集（缺失队列 / 表外定时队列）；
      2) 定时项时间与开关漂移（Time 不符、Enabled=true 未关净）；
      3) 队列项（空队列 / 项指向不存在的脚本）；
      4) 槽位对一致性（日常定时N 与 晚班定时N 脚本集必须相同）与跨槽脚本重复；
      5) 噪音（非班次队列数）→ warnings。
    all_names 仅测试注入用（替代 queue_map() 的全量名字列表）。
    """
    table = schedule()
    snap = snapshot if snapshot is not None else snapshot_queues()
    try:
        tmr = timers if timers is not None else mas_timers()
    except Exception:  # noqa: BLE001
        tmr = {}
    problems, warnings, drift = [], [], []

    missing = sorted(n for n in table if n not in snap)
    extra = sorted(n for n, _v in snap.items() if n not in table)
    for n in missing:
        problems.append("缺失队列: %s（SHIFT_TABLE 有、AUTO-MAS 无）" % n)
    for n in extra:
        problems.append("表外队列: %s（AUTO-MAS 有定时队列、SHIFT_TABLE 无）" % n)

    for n in sorted(table):
        if n not in snap:
            continue  # 缺失已报，不再叠噪音
        t = tmr.get(n)
        if not t:
            warnings.append("无定时项记录: %s（读取失败或已删）" % n)
            continue
        if str(t.get("Time") or "") != table[n]:
            drift.append("%s MAS=%s 表=%s" % (n, t.get("Time"), table[n]))
        if t.get("Enabled"):
            drift.append("%s MAS 定时项 Enabled=True（应为 false）" % n)
    for d in drift:
        problems.append("时间/开关漂移: " + d)

    seen = {}
    for n in sorted(snap):
        v = snap[n]
        if v.get("error"):
            problems.append("%s: 队列项读取失败（%s）" % (n, v["error"]))
            continue
        if v.get("bad_items"):
            problems.append("%s: %d 个项指向不存在的脚本 %s" % (n, len(v["bad_items"]),
                                                          [str(x)[:8] for x in v["bad_items"][:3]]))
        if not v.get("count"):
            problems.append("%s: 队列为空（0 项）" % n)
        if n in table:
            for s in v["scripts"]:
                seen.setdefault(s["sid"], []).append(n)

    slots = {}
    for n in sorted(table):
        m = _N_RE.match(n)
        if m:
            slots.setdefault(int(m.group(1)), []).append(n)
    for k in sorted(slots):
        pair = slots[k]
        if len(pair) != 2:
            continue
        dq = next((x for x in pair if x.startswith("日常")), None)
        eq = next((x for x in pair if x.startswith("晚班")), None)
        if dq in snap and eq in snap:
            a = sorted(s["sid"] for s in (snap[dq]["scripts"] or []))
            b = sorted(s["sid"] for s in (snap[eq]["scripts"] or []))
            if a != b:
                problems.append("槽位对不一致: %s(%d) vs %s(%d) 脚本集不同" % (dq, len(a), eq, len(b)))
    for sid, qs in sorted(seen.items()):
        slot_tags = set()
        for q in qs:
            m = _N_RE.match(q)
            slot_tags.add(m.group(1) if m else q)
        if len(qs) > 1 and len(slot_tags) > 1:
            problems.append("脚本跨槽重复: %s 同时出现在 %s" % (sid[:8], ", ".join(qs)))

    others = sorted(n for n in (all_names if all_names is not None else queue_map())
                    if n and not QUEUE_RE.match(n))
    if others:
        warnings.append("非班次队列 %d 条（%s…）：不参与调度，按残留物看" % (len(others), ", ".join(others[:3])))

    return {"ok": not problems, "problems": problems, "warnings": warnings, "drift": drift,
            "brief": {"scheduled": len(table), "found": len(snap),
                      "missing": missing, "extra": extra}}


# ---------------------------------------------------------------------------
# 资源与冲突（同一 MAA 槽位/实例被两个队列同窗发车 => 拒绝 + 告警）
#   资源 = 队列全部脚本的 {MAA 目录, 脚本id, 模拟器实例}；busy 窗口 = 发车时刻 + HOLD_MIN。
#   结论只做保守拒绝：窗口内重叠即拒（--force-conflict 显式放行并记账）。
# ---------------------------------------------------------------------------

def resources_of(name: str, snap: dict | None = None) -> dict | None:
    """队列名 -> {scripts:[sid], dirs:[d], emus:[e], names:[..]}；解析失败 None（fail-closed 用）。"""
    snap = snap if snap is not None else snapshot_queues(names={name})
    v = snap.get(name)
    if not v or v.get("error"):
        return None
    return {"scripts": sorted(s["sid"] for s in v["scripts"]),
            "dirs": sorted({s["dir"] for s in v["scripts"] if s["dir"]}),
            "emus": sorted({s["emu"] for s in v["scripts"] if s["emu"]}),
            "names": [s["name"] for s in v["scripts"]]}


def _parse_ts(s):
    try:
        return dt.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def busy_until_of(rec: dict, hold_min: int = HOLD_MIN) -> dt.datetime | None:
    ts = _parse_ts(rec.get("busy_until"))
    if ts:
        return ts
    at = _parse_ts(rec.get("at"))
    if at:
        return at + dt.timedelta(minutes=hold_min)
    return None


def find_conflicts(name: str, res: dict | None, day_states, now: dt.datetime,
                   hold_min: int = HOLD_MIN) -> list:
    """与 name 资源重叠且仍在 busy 窗口内的其它已发队列（近两日状态）。

    条目无 resources（旧版写入）时退回槽位启发式：同槽位 N 视为冲突，异槽位放行。
    """
    out = []
    my_slot = _N_RE.match(name)
    my_slot = int(my_slot.group(1)) if my_slot else None
    for day in day_states:
        if not isinstance(day, dict):
            continue
        for other, rec in day.items():
            if other == name or not isinstance(rec, dict) or not rec.get("taskId"):
                continue
            until = busy_until_of(rec, hold_min)
            if not until or until <= now:
                continue
            ores = rec.get("resources") or {}
            if ores and res is not None:
                shared = {
                    "dirs": sorted(set(res.get("dirs") or []) & set(ores.get("dirs") or [])),
                    "scripts": sorted(set(res.get("scripts") or []) & set(ores.get("scripts") or [])),
                    "emus": sorted(set(res.get("emus") or []) & set(ores.get("emus") or [])),
                }
                if not (shared["dirs"] or shared["scripts"] or shared["emus"]):
                    continue
            else:
                m = _N_RE.match(other)
                o_slot = int(m.group(1)) if m else None
                if my_slot is None or o_slot is None or my_slot != o_slot:
                    continue  # 异槽位在结构上资源不重叠；无法判定时按不冲突处理并留 note
                shared = {"dirs": [], "scripts": [], "emus": [], "unknown_resources": True}
            out.append({"queue": other, "dispatched_at": rec.get("at"),
                        "busy_until": until.strftime("%Y-%m-%d %H:%M:%S"), "shared": shared})
    return out


# ---------------------------------------------------------------------------
# 补发边界（与巡检补跑的分工）
#   规则：本脚本只在 grace 窗口内自动发车；超出窗口只记 late 标记 + 告警，
#   补发一律由人/巡检经 dispatch_queue（同一单飞状态 + 并发锁）显式执行 ⇒ 不会双发。
#   late 标记落 state["late"][date][queue]（--dismiss-late 关闭「决定不补」）。
# ---------------------------------------------------------------------------

def late_marker_set(st: dict, date: str, name: str, scheduled: str, now: dt.datetime) -> bool:
    """登记/刷新 late 标记；首次返回 True。"""
    late = st.setdefault("late", {}).setdefault(date, {})
    e = late.get(name)
    if e and e.get("scheduled") == scheduled:
        e["last_seen"] = now.strftime("%Y-%m-%d %H:%M:%S")
        return False
    late[name] = {"scheduled": scheduled, "first_seen": now.strftime("%Y-%m-%d %H:%M:%S"),
                  "last_seen": now.strftime("%Y-%m-%d %H:%M:%S")}
    return True


def late_marker_clear(st: dict, name: str, date: str | None = None, reason: str = "dispatched") -> bool:
    """关闭 late 标记（补发成功 / 决定不补）；返回是否命中。"""
    late = st.get("late") or {}
    hit = False
    for d, m in list(late.items()):
        if date and d != date:
            continue
        if name in (m or {}):
            m.pop(name, None)
            hit = True
        if not m:
            late.pop(d, None)
    return hit


def late_report(st: dict, now: dt.datetime | None = None) -> list:
    """未关闭的 late 标记列表（补发决策视图）。"""
    now = now or dt.datetime.now()
    out = []
    for d, m in sorted((st.get("late") or {}).items()):
        for name, e in sorted((m or {}).items()):
            fs = _parse_ts(e.get("first_seen"))
            out.append({"queue": name, "date": d, "scheduled": e.get("scheduled"),
                        "first_seen": e.get("first_seen"), "last_seen": e.get("last_seen"),
                        "age_min": int((now - fs).total_seconds() // 60) if fs else None})
    return out


def busy_entries(st: dict, now: dt.datetime | None = None, hold_min: int = HOLD_MIN) -> list:
    """当前仍在 busy 窗口内的已发队列（跨今日/昨日）。"""
    now = now or dt.datetime.now()
    out = []
    for d in sorted((st or {}).keys()):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)):
            continue
        for name, rec in sorted((st.get(d) or {}).items()):
            if not isinstance(rec, dict) or not rec.get("taskId"):
                continue
            until = busy_until_of(rec, hold_min)
            if until and until > now:
                out.append({"queue": name, "date": d, "at": rec.get("at"), "taskId": rec.get("taskId"),
                            "busy_until": until.strftime("%Y-%m-%d %H:%M:%S"),
                            "dirs": (rec.get("resources") or {}).get("dirs") or []})
    return out


# ---------------------------------------------------------------------------
# 发车
# ---------------------------------------------------------------------------

def _audit_rec(name: str, uid: str, r: dict, *, attempt: int, retries_used: int,
               test: bool, terminal: bool = False, deferred: bool = False) -> dict:
    kind = "deferred" if deferred else ("dispatch_ok" if r.get("ok") else
                                       ("dispatch_failed" if terminal else "dispatch_attempt"))
    rec = {"ts": _now(), "queue": name, "queue_id": uid, "mode": "AutoProxy",
           "ok": bool(r.get("ok")), "taskId": r.get("taskId"), "reason": r.get("reason"),
           "retryable": bool(r.get("retryable")), "attempt": attempt, "retries_used": retries_used,
           "terminal": bool(terminal), "detail": (r.get("detail") or "")[:500], "resp": r.get("resp"),
           "kind": kind, "final": ("ok" if r.get("ok") else ("deferred" if deferred else
                                                             ("terminal" if terminal else "retry_pending")))}
    if test:
        rec["test"] = True
    if deferred:
        rec["deferred"] = True
    return rec


def dispatch(name: str, uid: str, apply_: bool, force: bool = False, st: dict | None = None, *,
             post_id: str | None = None, test: bool = False, retries: int | None = None,
             retry_delays=None, retry_budget: int | None = None, use_lock: bool = True,
             force_conflict: bool = False) -> dict:
    """单条发车（默认 dry-run）。返回结构化结果。

    - 真发车：并发锁 + 当天单飞复检 + **冲突闸门**（同 MAA 目录/脚本/实例被另一队列同窗占用
      => 拒绝 conflict_inflight 并告警；--force-conflict 显式放行并记账）-> 尝试（可重试类按
      退避重试，受预算约束）；
    - 终态失败：写审计 + 告警台账，结果里带 alert='<queue>:<reason>'；
    - 成功且此前有告警：结果里带 alert_cleared；成功同时刷新 busy 窗口（HOLD_MIN）并关闭 late 标记；
    - test=True（--task-id 自测注入）时日志/台账带 test 标记（巡检视图排除）。
    """
    today = dt.date.today().isoformat()
    if not QUEUE_RE.match(name):
        return {"queue": name, "ok": False, "refused": "not_whitelisted"}
    if NO_GO and apply_:
        return {"queue": name, "ok": False, "refused": "dispatch_disabled"}
    if not apply_:
        ok, why = reachable()
        if not ok:
            return {"queue": name, "ok": False, "refused": "upstream_unreachable",
                    "reason": "rest_unreachable", "detail": why}
        qid = post_id or uid
        if not qid:
            with contextlib.suppress(Exception):  # dry-run 尽力把队列 uid 也显示出来
                qid = queue_map().get(name, "") or ""
        return {"queue": name, "ok": True, "dry_run": True, "would_dispatch": name, "queue_id": qid}

    post_target = post_id or uid
    need_resolve = not post_target
    n_retries = max(0, retries if retries is not None else RETRIES)
    delays = list(retry_delays if retry_delays is not None else RETRY_DELAYS)
    budget = retry_budget if retry_budget is not None else RETRY_BUDGET_S
    total = 1 + n_retries

    try:
        with _dispatch_lock(enabled=use_lock):
            # 锁内复检当天单飞（跨进程；cron / MCP / 手动三路并发时防双发）
            st2 = load_state()
            day = st2.setdefault(today, {})
            if not force and name in day:
                return {"queue": name, "ok": False, "refused": "duplicate_today", "first": day[name]}

            # 冲突闸门：本队列资源与「近两日已发且仍在 busy 窗口内」的其它队列重叠 => 拒发 + 告警
            # 优化：只有存在候选对手（近两日有其它队列发车记录）才去解析本方资源，
            # 避免无意义的只读请求（也保证离线测试里不额外消耗传输桩）。
            conf = []
            res_map = None
            prev = [st2.get((dt.date.today() - dt.timedelta(days=k)).isoformat()) or {} for k in (0, 1)]
            candidates = [q for d in prev for q, r in d.items() if q != name and isinstance(r, dict)]
            if candidates:
                with contextlib.suppress(Exception):
                    res_map = resources_of(name)
                with contextlib.suppress(Exception):
                    conf = find_conflicts(name, res_map, prev, dt.datetime.now())
            if conf and not force_conflict:
                detail = "资源重叠: %s（%s 发车，busy 至 %s；shared dirs=%s scripts=%d）" % (
                    ", ".join(c["queue"] for c in conf), conf[0].get("dispatched_at"),
                    conf[0].get("busy_until"), ",".join(conf[0]["shared"].get("dirs") or [])[:160],
                    len(conf[0]["shared"].get("scripts") or []))
                audit({"ts": _now(), "kind": "dispatch_refused", "queue": name, "queue_id": uid,
                       "refused": "conflict_inflight", "detail": detail, "conflict": conf,
                       "resources": res_map})
                alert = alert_fail(name, "conflict_inflight", detail, 0, test=test)
                return {"queue": name, "ok": False, "dispatched": False,
                        "refused": "conflict_inflight", "conflict": conf, "detail": detail,
                        "alert": alert}

            last: dict = {}
            rec: dict = {}
            attempts_done = 0
            for i in range(total):
                attempts_done = i + 1
                last = _attempt(name, post_target, need_resolve)
                if last.get("post_target"):
                    post_target = last["post_target"]
                    need_resolve = False
                retr = bool(last.get("retryable"))
                last_attempt = (i == total - 1)
                defer = False
                w = 0
                if not last.get("ok") and retr and not last_attempt:
                    w = delays[min(i, len(delays) - 1)] if delays else 0
                    if w and (time.time() - _PROC_START) + w > budget:
                        defer = True
                if last.get("ok"):
                    entry = {"shift": shift_of(name), "at": _now(),
                             "taskId": last.get("taskId"), "attempts": attempts_done,
                             "resources": res_map,
                             "busy_until": (dt.datetime.now() + dt.timedelta(minutes=HOLD_MIN)
                                            ).strftime("%Y-%m-%d %H:%M:%S")}
                    if conf and force_conflict:
                        entry["conflict_overridden"] = [c["queue"] for c in conf]
                    day[name] = entry
                    late_closed = late_marker_clear(st2, name)
                    save_state(st2)
                    if isinstance(st, dict):  # 让调用方手里的 state 同步刷新
                        st.setdefault(today, {})[name] = day[name]
                    if late_closed:
                        audit({"ts": _now(), "kind": "late_resolved", "queue": name, "queue_id": uid,
                               "by": "dispatch", "test": True if test else None})
                    cleared = alert_clear(name, test=test)
                    audit(_audit_rec(name, uid, last, attempt=attempts_done, retries_used=i,
                                     test=test, terminal=False))
                    out = {"queue": name, "ok": True, "dispatched": True, "taskId": last.get("taskId"),
                           "attempts": attempts_done, "retries": i, "resp": last.get("resp")}
                    if conf and force_conflict:
                        out["conflict_overridden"] = entry["conflict_overridden"]
                    if late_closed:
                        out["late_closed"] = True
                    if cleared:
                        out["alert_cleared"] = ",".join(cleared)
                    return out
                rec = _audit_rec(name, uid, last, attempt=attempts_done, retries_used=i,
                                 test=test, terminal=((not retr) or last_attempt) and not defer,
                                 deferred=defer)
                audit(rec)
                if retr and not last_attempt and not defer:
                    if w:
                        time.sleep(w)
                    continue
                break

            if rec.get("deferred"):
                return {"queue": name, "ok": False, "dispatched": False, "deferred": True,
                        "reason": last.get("reason"), "detail": last.get("detail"),
                        "attempts": attempts_done}
            alert = alert_fail(name, str(last.get("reason")), str(last.get("detail") or ""),
                               attempts_done, test=test)
            return {"queue": name, "ok": False, "dispatched": False, "failed": True,
                    "reason": last.get("reason"), "class": "terminal",
                    "detail": last.get("detail"), "attempts": attempts_done,
                    "alert": alert, "resp": last.get("resp")}
    except _LockBusy as e:
        return {"queue": name, "ok": False, "refused": "concurrent_dispatch", "detail": str(e)}


def dispatch_summary(res: dict) -> str:
    """一行摘要（cron/巡检日志用）。"""
    if res.get("dispatched"):
        return "SENT %s taskId=%s retries=%s%s" % (
            res.get("queue"), str(res.get("taskId"))[:8], res.get("retries"),
            " late=closed" if res.get("late_closed") else "")
    if res.get("dry_run"):
        return "DRY  %s would_dispatch" % res.get("queue")
    if res.get("refused") == "conflict_inflight":
        return "SKIP %s conflict_inflight（与 %s 资源重叠，已告警；确认后 --force-conflict 放行）" % (
            res.get("queue"), ", ".join(str(c.get("queue")) for c in (res.get("conflict") or [])))
    if res.get("deferred"):
        return "DEFER %s %s（重试预算用尽，下一跳继续）" % (res.get("queue"), res.get("detail"))
    if res.get("refused") == "concurrent_dispatch":
        return "SKIP %s %s（并发锁被占，下一跳继续）" % (res.get("queue"), res.get("detail"))
    return "FAIL %s -> %s" % (res.get("queue"), json.dumps(res, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def _emit(obj, as_json: bool, text: str = "") -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False))
    elif text:
        print(text)


def cmd_list(as_json: bool = False) -> int:
    ok, why = reachable()
    table = schedule()
    st = load_state().get(dt.date.today().isoformat(), {})
    try:
        mt = mas_timers()
    except Exception:  # noqa: BLE001
        mt = {}
    drift = [n for n, t in table.items() if n in mt and (mt[n]["Time"] != t or mt[n]["Enabled"])]
    alerts = []
    try:
        alerts = alert_lines()
    except Exception:  # noqa: BLE001
        pass
    if as_json:
        _emit({"reachable": ok, "detail": why, "scheduled_queues": len(table), "sent_today": sorted(st.keys()),
               "mas_timers_count": len(mt), "drift": sorted(drift), "no_go": NO_GO, "grace_min": GRACE_MIN,
               "alerts": alerts, "retry": {"retries": RETRIES, "delays": RETRY_DELAYS, "budget_s": RETRY_BUDGET_S}},
              as_json)
    else:
        print("AUTO-MAS: %s  (%s)" % ("reachable" if ok else "UNREACHABLE", why))
        print("scheduled queues: %d (Hermes 持有班次表) | MAS 侧定时项: %d | 漂移/未关: %s"
              % (len(table), len(mt), (", ".join(sorted(drift)) if drift else "无")))
        for name in sorted(table, key=lambda n: (shift_of(n), table[n])):
            mark = "SENT" if name in st else "    "
            print("  %-4s %s  %-12s %s" % (mark, table[name], name, "" if name not in st else st[name].get("taskId", "")))
        print("NO_GO=%s GRACE=%dmin RETRIES=%d/%s budget=%ds | alerts: %s"
              % (NO_GO, GRACE_MIN, RETRIES, "/".join(str(d) for d in RETRY_DELAYS), RETRY_BUDGET_S,
                 ("; ".join(alerts) if alerts else "无")))
    return 0 if ok else 2


def cmd_check(as_json: bool = False) -> int:
    problems, warnings = [], []
    ok, why = reachable()
    if not ok:
        problems.append("AUTO-MAS 不可达: %s" % why)
    table = schedule()
    if len(table) != 24:
        problems.append("班次表数=%d（应为 24）" % len(table))
    for n in table:
        if not QUEUE_RE.match(n):
            problems.append("白名单外队列: %s" % n)
    if NO_GO:
        warnings.append("警告：MAAORCH_DISPATCH_NO_GO 已置位，真发车会被拒（演练模式）")
    if RETRIES and (not RETRY_DELAYS or len(RETRY_DELAYS) < RETRIES):
        warnings.append("警告：RETRY_DELAYS(%s) 短于 RETRIES(%d)，将复用末位等待" % (RETRY_DELAYS, RETRIES))
    try:
        all_active = active_alerts(exclude_test=False)
        tst = [e for e in all_active if e.get("test")]
        if tst:
            warnings.append("警告：台账里有 %d 条 test 自测告警（可 --clear-test-alerts 回收）" % len(tst))
    except Exception as e:  # noqa: BLE001
        problems.append("告警台账读取失败: %s" % e)
    if os.path.exists(LOCK):
        try:
            age = time.time() - os.path.getmtime(LOCK)
        except OSError:
            age = 0.0
        if age > LOCK_STALE_S:
            warnings.append("警告：存在陈旧锁 %s（%.0fs，可被下一跳抢锁）" % (LOCK, age))
        else:
            warnings.append("警告：锁 %s 正被占（%.0fs，有并发发车进行中）" % (LOCK, age))
    if as_json:
        _emit({"ok": not problems, "problems": problems, "warnings": warnings,
               "retry": {"retries": RETRIES, "delays": RETRY_DELAYS, "budget_s": RETRY_BUDGET_S},
               "alerts_active": len(active_alerts()) if not problems else None}, as_json)
    else:
        print("mas_dispatch --check: %s" % ("FAIL" if problems else "OK"))
        print("  retry: retries=%d delays=%s budget=%ds | alerts active=%d | lock=%s"
              % (RETRIES, "/".join(str(d) for d in RETRY_DELAYS), RETRY_BUDGET_S,
                 len(active_alerts()), "held" if os.path.exists(LOCK) else "free"))
        for p in problems:
            print("  - " + p)
        for w in warnings:
            print("  - " + w)
    return 2 if problems else 0


def cmd_reconcile(apply_: bool, force: bool, as_json: bool = False, **over) -> int:
    table = schedule()
    now = dt.datetime.now()
    st = load_state()
    day = st.setdefault(dt.date.today().isoformat(), {})
    lines, actions = [], []
    alerts, cleared = [], []
    dirty = False
    for name in sorted(table, key=lambda n: table[n]):
        hh, mm = table[name].split(":")
        due = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if now < due:
            continue
        if name in day:
            continue
        if (now - due).total_seconds() > GRACE_MIN * 60:
            lines.append("LATE %s 计划 %s 已超追发窗口 %d 分钟 —— 不自动发车；已交巡检/人工决策"
                         "（补发须显式 dispatch_queue，本脚本不补）" % (name, table[name], GRACE_MIN))
            act = {"queue": name, "scheduled": table[name], "late": True, "handoff": "patrol_or_human"}
            if apply_:
                if late_marker_set(st, dt.date.today().isoformat(), name, table[name], now):
                    act["marker"] = "created"
                    dirty = True
                    audit({"ts": _now(), "kind": "late_handoff", "queue": name,
                           "scheduled": table[name], "grace_min": GRACE_MIN})
                alert = alert_fail(name, "late_handoff",
                                   "超追发窗口 %d 分钟（计划 %s）未发车，待巡检/人工决策"
                                   % (GRACE_MIN, table[name]), 0)
                act["alert"] = alert
                lines.append("DISPATCH_ALERT=%s" % alert)
                alerts.append(alert)
            actions.append(act)
            continue
        res = dispatch(name, "", apply_, force, st, **over)
        actions.append({"queue": name, "scheduled": table[name], "result": res})
        lines.append(dispatch_summary(res))
        if res.get("alert"):
            lines.append("DISPATCH_ALERT=%s" % res["alert"])
            alerts.append(res["alert"])
        if res.get("alert_cleared"):
            lines.append("DISPATCH_ALERT_CLEARED=%s" % res["alert_cleared"])
            cleared.append(res["alert_cleared"])
    if dirty:
        save_state(st)
    if as_json:
        _emit({"action": "reconcile", "apply": bool(apply_), "now": now.strftime("%Y-%m-%d %H:%M:%S"),
               "count": len(actions), "actions": actions, "alerts": alerts,
               "alerts_cleared": cleared,
               "late_handoffs": [a["queue"] for a in actions if a.get("handoff")]}, as_json)
    elif lines:
        print("\n".join(lines))
    return 0


def cmd_queue(name: str, apply_: bool, force: bool, as_json: bool = False, **over) -> int:
    qm = None
    with contextlib.suppress(Exception):
        qm = queue_map()
    if qm is not None and name not in qm:
        msg = {"ok": False, "refused": "unknown_queue", "queue": name,
               "detail": "队列名必须是 日常定时N / 晚班定时N（N=1..12）且在 AUTO-MAS 存在"}
        _emit(msg, as_json, "unknown queue: %s" % name)
        return 2
    # qm 拿不到（REST 不可达）就交给 dispatch 内部解析：走分类 + 退避重试 + 告警
    res = dispatch(name, (qm or {}).get(name, ""), apply_, force, **over)
    if as_json:
        _emit(res, as_json)
    else:
        print(dispatch_summary(res))
        if res.get("alert"):
            print("DISPATCH_ALERT=%s" % res["alert"])
        if res.get("alert_cleared"):
            print("DISPATCH_ALERT_CLEARED=%s" % res["alert_cleared"])
    return 0


def cmd_shift(shift: str, apply_: bool, force: bool, stagger: int, as_json: bool = False, **over) -> int:
    table = {n: t for n, t in schedule().items() if shift_of(n) == shift}
    qm = {}
    with contextlib.suppress(Exception):
        qm = queue_map()
    st = load_state()
    results = []
    for i, name in enumerate(sorted(table, key=lambda n: table[n])):
        if i and apply_:
            time.sleep(stagger)
        res = dispatch(name, qm.get(name, ""), apply_, force, st, **over)
        results.append(res)
        if not as_json:
            print(dispatch_summary(res))
            if res.get("alert"):
                print("DISPATCH_ALERT=%s" % res["alert"])
            if res.get("alert_cleared"):
                print("DISPATCH_ALERT_CLEARED=%s" % res["alert_cleared"])
    if as_json:
        _emit({"action": "shift", "shift": shift, "apply": bool(apply_), "results": results}, as_json)
    return 0


def cmd_alerts(include_test: bool = False, as_json: bool = False) -> int:
    al = active_alerts(exclude_test=not include_test)
    if as_json:
        _emit({"active": al}, as_json)
        return 0
    if not al:
        print("alerts: 无")
        return 0
    print("alerts: %d 条未消除" % len(al))
    for e in al:
        print("  %-12s %-22s attempts=%s ts=%s%s\n      %s" % (
            e.get("queue"), e.get("reason"), e.get("attempts"), e.get("ts"),
            "  [test]" if e.get("test") else "", e.get("detail")))
    return 0


def cmd_clear_test_alerts(as_json: bool = False) -> int:
    n = clear_test_alerts()
    _emit({"cleared_test_alerts": n}, as_json, "test alerts cleared: %d" % n)
    return 0


# ---------------------------------------------------------------------------
# 新增命令：一致性校验 / busy 视图 / late 处置（补发边界）
# ---------------------------------------------------------------------------

def cmd_check_schedule(as_json: bool = False) -> int:
    try:
        rep = check_schedule()
    except Exception as e:  # noqa: BLE001
        rep = {"ok": False, "problems": ["检查执行失败: %s: %s" % (type(e).__name__, e)],
               "warnings": [], "drift": [], "brief": {}}
    if as_json:
        _emit(rep, as_json)
    else:
        print("mas_dispatch --check-schedule: %s" % ("OK" if rep["ok"] else "FAIL"))
        print("  brief: %s" % json.dumps(rep.get("brief"), ensure_ascii=False))
        for p in rep["problems"]:
            print("  - " + p)
        for w in rep["warnings"]:
            print("  ~ " + w)
    return 0 if rep["ok"] else 2


def cmd_busy(as_json: bool = False) -> int:
    st = load_state()
    now = dt.datetime.now()
    busy = busy_entries(st, now)
    late = late_report(st, now)
    if as_json:
        _emit({"now": now.strftime("%Y-%m-%d %H:%M:%S"), "hold_min": HOLD_MIN,
               "busy": busy, "late": late}, as_json)
        return 0
    print("busy 窗口（HOLD_MIN=%dmin）：%d 条" % (HOLD_MIN, len(busy)))
    for b in busy:
        print("  %-12s %s -> busy 至 %s  dirs=%s" % (
            b["queue"], b["at"], b["busy_until"],
            ",".join(os.path.basename(d) for d in b["dirs"]) or "(未记录)"))
    print("late 标记：%d 条" % len(late))
    for lo in late:
        print("  %-12s %s 计划 %s first_seen=%s age=%smin" % (
            lo["queue"], lo["date"], lo["scheduled"], lo["first_seen"], lo["age_min"]))
    return 0


def cmd_release(queue: str, apply_: bool, as_json: bool = False) -> int:
    """人工确认某队列任务已结束后，清掉它的 busy 窗口（解锁同资源补发）。

    兼容两种条目：带 busy_until 的新条目，以及只有 at 的旧条目（后者 busy_until 按
    at+HOLD_MIN 推算——release 会显式写入过去时刻的 busy_until 以解除占用）。
    """
    st = load_state()
    now = dt.datetime.now()
    hit = None
    for d, recs in sorted((st or {}).items()):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)):
            continue
        rec = (recs or {}).get(queue)
        if isinstance(rec, dict) and (rec.get("busy_until") or rec.get("at")):
            hit = (d, rec)
    if not hit:
        _emit({"queue": queue, "released": False, "detail": "无发车记录"}, as_json,
              "no busy entry for %s" % queue)
        return 2
    d, rec = hit
    if not apply_:
        _emit({"queue": queue, "released": False, "dry_run": True, "date": d,
               "at": rec.get("at"), "busy_until": rec.get("busy_until")}, as_json,
              "DRY release %s（%s at=%s busy_until=%s）——加 --apply 生效"
              % (queue, d, rec.get("at"), rec.get("busy_until") or "（旧条目，按 at+HOLD_MIN 推算）"))
        return 0
    prev_busy = rec.get("busy_until")
    rec["busy_until"] = (now - dt.timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    rec["released_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    rec["released_by"] = "manual_release"
    save_state(st)
    audit({"ts": _now(), "kind": "busy_release", "queue": queue, "date": d,
           "prev_busy_until": prev_busy})
    _emit({"queue": queue, "released": True, "date": d}, as_json,
          "release %s：busy 窗口已清（%s）" % (queue, rec["released_at"]))
    return 0


def cmd_dismiss_late(queue: str, apply_: bool, note: str = "", as_json: bool = False) -> int:
    """关闭 late 标记（决定不补）。清对应告警（cleared_by=dismissed）并记审计。"""
    st = load_state()
    now = dt.datetime.now()
    hit = [lo for lo in late_report(st, now) if lo["queue"] == queue]
    if not hit:
        _emit({"queue": queue, "dismissed": False, "detail": "无未关闭 late 标记"}, as_json,
              "no open late marker for %s" % queue)
        return 2
    if not apply_:
        _emit({"queue": queue, "dismissed": False, "dry_run": True, "marker": hit[0]}, as_json,
              "DRY dismiss %s（标记 %s 计划 %s）——加 --apply 生效%s" % (
                  queue, hit[0]["date"], hit[0]["scheduled"], ("；note=" + note) if note else ""))
        return 0
    late_marker_clear(st, queue)
    save_state(st)
    cleared = alert_clear(queue, test=False, cleared_by="dismissed")
    audit({"ts": _now(), "kind": "late_dismissed", "queue": queue, "note": note[:300],
           "alerts_cleared": cleared})
    _emit({"queue": queue, "dismissed": True, "alerts_cleared": cleared}, as_json,
          "dismiss %s：late 标记已关%s%s" % (
              queue, ("；告警已清 %s" % ",".join(cleared)) if cleared else "",
              ("；note=" + note) if note else ""))
    return 0


# ---------------------------------------------------------------------------
# 离线自测（假传输；不碰网络、不碰真 runtime）
# ---------------------------------------------------------------------------

def selftest() -> dict:
    """核心场景离线回归。返回 {ok, passed, failed, cases:[{name, ok, detail}]}。"""
    import shutil
    import tempfile

    cases = []

    def add(name, fn):
        try:
            fn()
            cases.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e)})

    tmp = tempfile.mkdtemp(prefix="mas_dispatch_selftest_")
    saved = {k: globals()[k] for k in ("RUNTIME", "STATE", "LOG", "ALERTS", "LOCK")}
    saved_fns = {"post": post, "reachable": reachable, "NO_GO": NO_GO, "_PROC_START": _PROC_START,
                 "resources_of": resources_of, "snapshot_queues": snapshot_queues,
                 "queue_map": queue_map, "schedule": schedule}
    calls = {"post": 0}
    RES_MAP = {}

    def _fake_resources(name, snap=None):
        """测试资源表：RES_MAP[name] -> dict|None（None=退回槽位启发式）。"""
        return RES_MAP.get(name)

    globals()["resources_of"] = _fake_resources

    def set_runtime():
        globals()["RUNTIME"] = tmp
        globals()["STATE"] = os.path.join(tmp, "state.json")
        globals()["LOG"] = os.path.join(tmp, "log.jsonl")
        globals()["ALERTS"] = os.path.join(tmp, "alerts.json")
        globals()["LOCK"] = os.path.join(tmp, "lock")

    def script(reach_seq, post_seq):
        """给 reachable()/post() 灌脚本；reach_seq=[(ok,why)...] post_seq=[dict|Exception...]。"""
        reach_seq = list(reach_seq)
        post_seq = list(post_seq)

        def _reach():
            return reach_seq.pop(0) if reach_seq else (True, "code=200")

        def _post(path, body=None, timeout=30):
            calls["post"] += 1
            item = post_seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        globals()["reachable"] = _reach
        globals()["post"] = _post

    def reset():
        set_runtime()
        globals()["NO_GO"] = False
        globals()["_PROC_START"] = time.time()
        RES_MAP.clear()
        for f in ("state.json", "log.jsonl", "alerts.json", "lock",
                  "state.json.tmp", "alerts.json.tmp"):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(tmp, f))

    OKR = {"code": 200, "status": "success", "message": "操作成功", "taskId": "task-ok"}
    RUNNING = {"code": 500, "status": "error", "message": "RuntimeError: 任务 X 已在运行", "taskId": ""}
    LOCKED = {"code": 500, "status": "error", "message": "任务 官-1-1-7 已被其他任务调度器锁定", "taskId": ""}
    NOTFOUND = {"code": 500, "status": "error",
                "message": "ValueError: 任务 00000000-0000-0000-0000-000000000000 无法找到对应脚本配置", "taskId": ""}
    OTHER500 = {"code": 500, "status": "error", "message": "KeyError: 'foo'", "taskId": ""}
    NOTASK = {"code": 200, "status": "success", "message": "操作成功", "taskId": ""}

    def Q(**kw):
        kw.setdefault("name", "日常定时1")
        kw.setdefault("uid", "uid-1")
        kw.setdefault("apply_", True)
        kw.setdefault("retries", 0)
        kw.setdefault("retry_delays", [0])
        kw.setdefault("use_lock", False)
        return dict(kw)

    def check(cond, msg):
        if not cond:
            raise AssertionError(msg)

    def c_ok():
        reset()
        script([(True, "code=200")], [OKR])
        r = dispatch(**Q())
        check(r.get("dispatched"), "应发车成功: %r" % r)
        check(not r.get("alert"), "成功不应有告警")
        check(load_state()[dt.date.today().isoformat()]["日常定时1"]["taskId"] == OKR["taskId"], "state 未写")

    def c_dry():
        reset()
        script([(True, "code=200")], [])
        calls["post"] = 0
        r = dispatch(**Q(apply_=False))
        check(r.get("dry_run"), "应为 dry-run: %r" % r)
        check(calls["post"] == 0, "dry-run 不应发 dispatch POST")

    def c_retry_ok():
        reset()
        script([(False, "URLError: refused"), (True, "code=200")], [OKR])
        r = dispatch(**Q(retries=3))
        check(r.get("dispatched") and r.get("retries") == 1, "应重试一次后成功: %r" % r)

    def c_retry_dead():
        reset()
        script([(False, "URLError: refused")] * 4, [])
        r = dispatch(**Q(retries=3))
        check(r.get("failed") and r.get("reason") == "rest_unreachable", "应为终态 rest_unreachable: %r" % r)
        check(r.get("alert") == "日常定时1:rest_unreachable", "告警串不对: %r" % r)
        check(alert_lines() == ["日常定时1:rest_unreachable"], "台账未记: %r" % active_alerts())
        check(r.get("attempts") == 4, "attempts 应为 4: %r" % r)

    def c_running_ok():
        reset()
        script([(True, "code=200"), (True, "code=200")], [RUNNING, OKR])
        r = dispatch(**Q(retries=3))
        check(r.get("dispatched") and r.get("retries") == 1, "target_running 应可重试成功: %r" % r)

    def c_slot_clear():
        reset()
        script([(True, "code=200")] * 2, [LOCKED, LOCKED])
        r = dispatch(**Q(retries=1))
        check(r.get("reason") == "slot_occupied" and r.get("alert") == "日常定时1:slot_occupied",
              "应 slot_occupied 告警: %r" % r)
        script([(True, "code=200")], [OKR])
        r2 = dispatch(**Q(force=True))
        check(r2.get("dispatched") and r2.get("alert_cleared") == "日常定时1:slot_occupied",
              "成功应自愈清告警: %r" % r2)
        check(active_alerts() == [], "台账应清空: %r" % active_alerts())

    def c_notaskid():
        reset()
        script([(True, "code=200")] * 3, [NOTASK])
        r = dispatch(**Q(retries=3))
        check(r.get("reason") == "no_taskid" and r.get("attempts") == 1, "no_taskid 不应重试: %r" % r)
        check(r.get("alert") == "日常定时1:no_taskid", "应告警: %r" % r)

    def c_badresp():
        reset()
        script([(True, "code=200")], [OTHER500])
        r = dispatch(**Q(retries=3))
        check(r.get("reason") == "bad_response" and r.get("attempts") == 1, "应 bad_response 且不重试: %r" % r)

    def c_notfound():
        reset()
        script([(True, "code=200")], [NOTFOUND])
        r = dispatch(**Q(retries=3))
        check(r.get("reason") == "target_not_found" and r.get("attempts") == 1, "应 target_not_found: %r" % r)

    def c_dedup():
        reset()
        script([(True, "code=200")], [OTHER500])
        dispatch(**Q())
        script([(True, "code=200")], [OTHER500])
        dispatch(**Q(force=True))
        check(len(active_alerts()) == 1, "台账应去重成 1 条: %r" % active_alerts())

    def c_lock():
        reset()
        with open(os.path.join(tmp, "lock"), "w", encoding="utf-8") as f:
            f.write("pid=999999 ts=selftest\n")
        script([(True, "code=200")], [OKR])
        r = dispatch(**Q(use_lock=True))
        check(r.get("refused") == "concurrent_dispatch", "持锁应拒: %r" % r)
        old = time.time() - (LOCK_STALE_S + 60)
        os.utime(os.path.join(tmp, "lock"), (old, old))
        r2 = dispatch(**Q(use_lock=True))
        check(r2.get("dispatched"), "陈旧锁应被抢: %r" % r2)
        check(not os.path.exists(os.path.join(tmp, "lock")), "锁应在退出时释放")

    def c_budget():
        reset()
        script([(False, "URLError: refused")], [])
        r = dispatch(**Q(retries=3, retry_delays=[5], retry_budget=0))
        check(r.get("deferred") and not r.get("alert"), "预算用尽应 defer 且不告警: %r" % r)
        check(active_alerts() == [], "defer 不应入台账")

    def c_prune():
        reset()
        old_date = (dt.date.today() - dt.timedelta(days=8)).isoformat()
        with open(os.path.join(tmp, "alerts.json"), "w", encoding="utf-8") as f:
            json.dump({"active": [{"queue": "日常定时2", "date": old_date, "reason": "rest_unreachable",
                                   "detail": "old", "ts": old_date, "attempts": 4, "test": False}],
                       "cleared": []}, f, ensure_ascii=False)
        alert_fail("日常定时3", "rest_unreachable", "new", 1)
        d = _alerts_load()
        check(len(d["active"]) == 1 and d["active"][0]["queue"] == "日常定时3", "过期条目未清: %r" % d)
        check(any(e.get("cleared_by") == "expired" for e in d["cleared"]), "应留 expired 历史")

    def c_testflag():
        reset()
        alert_fail("日常定时1", "rest_unreachable", "selftest", 4, test=True)
        check(active_alerts() == [], "test 条目应被巡检视图排除")
        check(len(active_alerts(exclude_test=False)) == 1, "test 条目应可被全量视图看到")
        check(clear_test_alerts() == 1, "test 回收计数不对")
        check(active_alerts(exclude_test=False) == [], "test 回收后应为空")

    def c_missing():
        reset()
        script([(True, "code=200")], [{"code": 200, "data": {}}])
        r = dispatch(**Q(uid="", retries=3))
        check(r.get("reason") == "queue_missing" and r.get("attempts") == 1, "应 queue_missing 不重试: %r" % r)
        check(r.get("alert") == "日常定时1:queue_missing", "应告警: %r" % r)

    def c_resolve_recover():
        reset()
        # 第 1 次解析队列映射时 REST 抖动；第 2 次解析成功 + 发车成功
        script([(True, "code=200"), (True, "code=200")],
               [urllib.error.URLError("connection reset"),
                {"code": 200, "data": {"uid-X": {"Info": {"Name": "日常定时1"}}}},
                OKR])
        r = dispatch(**Q(uid="", retries=3))
        check(r.get("dispatched") and r.get("retries") == 1, "解析抖动应重试成功: %r" % r)

    def c_resolve_dead():
        reset()
        script([(True, "code=200")] * 3, [urllib.error.URLError("down")] * 3)
        r = dispatch(**Q(uid="", retries=2))
        check(r.get("failed") and r.get("reason") == "rest_unreachable", "解析持续失败应终态: %r" % r)
        check(r.get("alert") == "日常定时1:rest_unreachable", "应告警: %r" % r)

    # ---- 加固专项（t_a53348ec）：一致性校验 / 冲突拒绝 / late 交接 / 状态自愈 ----

    def c_state_heal():
        reset()
        with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as f:
            f.write("{ not json at all")
        st = load_state()
        check(isinstance(st, dict) and not st, "损坏 state 应自愈为空: %r" % st)
        quar = [f for f in os.listdir(tmp) if f.startswith("state.json.corrupt-")]
        check(len(quar) == 1, "应隔离一份损坏文件: %r" % os.listdir(tmp))
        with open(os.path.join(tmp, "log.jsonl"), "r", encoding="utf-8") as f:
            check("state_quarantine" in f.read(), "审计应记 state_quarantine")
        script([(True, "code=200")], [OKR])
        r = dispatch(**Q())
        check(r.get("dispatched"), "自愈后应能正常发车: %r" % r)

    def c_state_shape_heal():
        reset()
        with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"2026-01-01": {"日常定时1": "junk"},
                       "late": {"2026-01-01": {"日常定时1": {"scheduled": "05:00"}}},
                       "meta": {"v": 2}}, f, ensure_ascii=False)
        st = load_state()
        check(st.get("2026-01-01") == {}, "非 dict 条目应被剔除: %r" % st)
        check("late" in st and "meta" in st, "late/meta 应保留: %r" % st)

    def c_conflict_refuse():
        reset()
        now = dt.datetime.now()
        today = dt.date.today().isoformat()
        save_state({today: {"晚班定时1": {
            "shift": "evening", "at": (now - dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
            "taskId": "peer-1",
            "resources": {"dirs": ["e:\\maa-multi\\maa_1", "e:\\maa-multi\\maa_2"],
                          "scripts": ["sid-a", "sid-b"], "emus": ["1", "2"]}}}})
        RES_MAP["日常定时1"] = {"dirs": ["e:\\maa-multi\\maa_1"], "scripts": ["sid-a"], "emus": ["1"]}
        script([(True, "code=200")], [OKR])  # 不应被消耗
        calls["post"] = 0
        r = dispatch(**Q())
        check(r.get("refused") == "conflict_inflight", "应冲突拒绝: %r" % r)
        check(calls["post"] == 0, "冲突拒绝不得发 POST（calls=%d）" % calls["post"])
        check(alert_lines() == ["日常定时1:conflict_inflight"], "应告警: %r" % alert_lines())
        script([(True, "code=200")], [OKR])
        r2 = dispatch(**Q(force_conflict=True))
        check(r2.get("dispatched") and r2.get("conflict_overridden") == ["晚班定时1"],
              "应放行并记账: %r" % r2)
        rec = load_state()[today]["日常定时1"]
        check(rec.get("conflict_overridden") == ["晚班定时1"], "state 应记 conflict_overridden: %r" % rec)
        check(rec.get("busy_until"), "成功应写 busy_until: %r" % rec)

    def c_conflict_expired_and_other_slot():
        reset()
        now = dt.datetime.now()
        today = dt.date.today().isoformat()
        old = now - dt.timedelta(minutes=HOLD_MIN + 30)
        save_state({today: {"晚班定时1": {
            "at": old.strftime("%Y-%m-%d %H:%M:%S"), "taskId": "peer-1",
            "resources": {"dirs": ["e:\\maa-multi\\maa_1"], "scripts": ["sid-a"], "emus": ["1"]},
            "busy_until": (old + dt.timedelta(minutes=HOLD_MIN)).strftime("%Y-%m-%d %H:%M:%S")}}})
        RES_MAP["日常定时1"] = {"dirs": ["e:\\maa-multi\\maa_1"], "scripts": ["sid-a"], "emus": ["1"]}
        script([(True, "code=200")], [OKR])
        r = dispatch(**Q())
        check(r.get("dispatched"), "busy 过期应放行: %r" % r)
        RES_MAP["日常定时2"] = {"dirs": ["e:\\maa-multi\\maa_3"], "scripts": ["sid-c"], "emus": ["3"]}
        script([(True, "code=200")], [OKR])
        r2 = dispatch(**Q(name="日常定时2"))
        check(r2.get("dispatched"), "异槽位不应冲突: %r" % r2)

    def c_conflict_legacy_heuristic():
        reset()
        now = dt.datetime.now()
        today = dt.date.today().isoformat()
        save_state({today: {"晚班定时1": {
            "at": (now - dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
            "taskId": "peer-legacy"}}})  # 旧条目：无 resources
        script([(True, "code=200")], [OKR])
        r = dispatch(**Q())  # 日常定时1 vs 晚班定时1 同槽位 => 保守拒绝
        check(r.get("refused") == "conflict_inflight", "旧条目同槽位应保守拒绝: %r" % r)
        check(r.get("conflict") and r["conflict"][0]["shared"].get("unknown_resources"),
              "应标 unknown_resources: %r" % (r.get("conflict") or []))
        RES_MAP["日常定时2"] = {"dirs": ["e:\\maa-multi\\maa_3"], "scripts": ["sid-c"], "emus": ["3"]}
        script([(True, "code=200")], [OKR])
        r2 = dispatch(**Q(name="日常定时2"))  # 异槽位 => 放行
        check(r2.get("dispatched"), "旧条目异槽位应放行: %r" % r2)

    def c_check_schedule_diff():
        snap = {
            "日常定时1": {"uid": "u1", "count": 2, "bad_items": [], "names": ["a", "b"],
                          "scripts": [{"sid": "s1", "name": "a", "dir": "e:\\m\\maa_1", "emu": "1"},
                                      {"sid": "s2", "name": "b", "dir": "e:\\m\\maa_2", "emu": "2"}]},
            "晚班定时1": {"uid": "u1e", "count": 1, "bad_items": [], "names": ["a"],
                          "scripts": [{"sid": "s1", "name": "a", "dir": "e:\\m\\maa_1", "emu": "1"}]},
            "日常定时2": {"uid": "u2", "count": 0, "bad_items": [], "scripts": [], "names": []},
        }
        tim = {"日常定时1": {"Enabled": False, "Time": "05:00"},
               "晚班定时1": {"Enabled": True, "Time": "19:31"},
               "日常定时2": {"Enabled": False, "Time": "05:03"}}
        rep = check_schedule(snap, tim, all_names=list(snap) + ["临时-x"])
        blob = "\n".join(rep["problems"])
        for needle in ("缺失队列", "时间/开关漂移", "槽位对不一致", "队列为空", "Enabled=True"):
            check(needle in blob, "应报 %s：%s" % (needle, blob))
        check(not rep["ok"], "应判 FAIL")
        check(any("非班次队列" in w for w in rep["warnings"]), "应提示临时队列: %r" % rep["warnings"])

    def c_check_schedule_ok():
        snap, tim = {}, {}
        for n, t in SHIFT_TABLE.items():
            m = _N_RE.match(n)
            slot = m.group(1) if m else n
            snap[n] = {"uid": n, "count": 1, "bad_items": [], "names": [n],
                       "scripts": [{"sid": "s" + slot, "name": n, "dir": "e:\\m\\maa_" + slot, "emu": slot}]}
            tim[n] = {"Enabled": False, "Time": t}
        rep = check_schedule(snap, tim, all_names=list(snap))
        check(rep["ok"], "全一致应 OK: %r" % rep["problems"])

    def c_late_handoff_flow():
        reset()
        now = dt.datetime.now()
        today = dt.date.today().isoformat()
        fake = {"日常定时1": (now - dt.timedelta(hours=3)).strftime("%H:%M"),
                "晚班定时1": (now + dt.timedelta(minutes=5)).strftime("%H:%M")}
        globals()["schedule"] = lambda: dict(fake)
        try:
            cmd_reconcile(True, False)
        finally:
            globals()["schedule"] = saved_fns["schedule"]
        st = load_state()
        check(st.get("late", {}).get(today, {}).get("日常定时1"), "应记 late 标记: %r" % st.get("late"))
        check("日常定时1:late_handoff" in alert_lines(), "LATE 应告警: %r" % alert_lines())
        first_seen = st["late"][today]["日常定时1"]["first_seen"]
        globals()["schedule"] = lambda: dict(fake)
        try:
            cmd_reconcile(True, False)
        finally:
            globals()["schedule"] = saved_fns["schedule"]
        st2 = load_state()
        check(st2["late"][today]["日常定时1"]["first_seen"] == first_seen, "重复跑不应改 first_seen")
        n_alert = len([a for a in active_alerts()
                       if a["reason"] == "late_handoff" and a["queue"] == "日常定时1"])
        check(n_alert == 1, "告警应去重（%d）" % n_alert)
        script([(True, "code=200")], [OKR])
        r = dispatch(**Q(force=True))
        check(r.get("dispatched") and r.get("late_closed"), "补发应关闭 late 标记: %r" % r)
        check("日常定时1" not in (load_state().get("late", {}).get(today, {}) or {}), "标记应清")
        check(alert_lines() == [], "告警应随补发清空: %r" % alert_lines())

    def c_dismiss_and_release():
        reset()
        now = dt.datetime.now()
        today = dt.date.today().isoformat()
        st = load_state()
        late_marker_set(st, today, "日常定时3", "05:06", now)
        save_state(st)
        alert_fail("日常定时3", "late_handoff", "selftest", 0)
        check(cmd_dismiss_late("日常定时3", True, "决定不补") == 0, "dismiss 应返回 0")
        check(not (load_state().get("late", {}).get(today, {}) or {}).get("日常定时3"), "标记应清")
        check(alert_lines() == [], "告警应清: %r" % alert_lines())
        st = load_state()
        st[today] = {"晚班定时9": {
            "at": now.strftime("%Y-%m-%d %H:%M:%S"), "taskId": "t9",
            "busy_until": (now + dt.timedelta(minutes=100)).strftime("%Y-%m-%d %H:%M:%S")}}
        save_state(st)
        check(len(busy_entries(load_state(), now)) == 1, "应见 1 条 busy")
        check(cmd_release("晚班定时9", True) == 0, "release 应返回 0")
        check(len(busy_entries(load_state(), now)) == 0, "release 后 busy 应清")
        # 旧条目（只有 at、无 busy_until）：release 也应能解除占用
        st = load_state()
        st[today] = {"晚班定时10": {"at": now.strftime("%Y-%m-%d %H:%M:%S"), "taskId": "t10"}}
        save_state(st)
        check(len(busy_entries(load_state(), now)) == 1, "旧条目应计入 busy")
        check(cmd_release("晚班定时10", True) == 0, "旧条目 release 应返回 0")
        check(len(busy_entries(load_state(), now)) == 0, "旧条目 release 后 busy 应清")

    add("ok_first_try", c_ok)
    add("dry_run_no_post", c_dry)
    add("retry_recover_unreachable", c_retry_ok)
    add("terminal_unreachable_alert", c_retry_dead)
    add("target_running_then_ok", c_running_ok)
    add("slot_occupied_alert_then_clear", c_slot_clear)
    add("no_taskid_terminal", c_notaskid)
    add("bad_response_terminal", c_badresp)
    add("target_not_found_terminal", c_notfound)
    add("alert_dedupe", c_dedup)
    add("lock_busy_and_stale", c_lock)
    add("budget_defer", c_budget)
    add("alerts_prune_expired", c_prune)
    add("test_flag_excluded", c_testflag)
    add("queue_missing_terminal", c_missing)
    add("resolve_recover_unreachable", c_resolve_recover)
    add("resolve_dead_alert", c_resolve_dead)
    add("state_corrupt_self_heal", c_state_heal)
    add("state_shape_normalize", c_state_shape_heal)
    add("conflict_inflight_refuse", c_conflict_refuse)
    add("conflict_expired_and_other_slot", c_conflict_expired_and_other_slot)
    add("conflict_legacy_slot_heuristic", c_conflict_legacy_heuristic)
    add("check_schedule_diff_detect", c_check_schedule_diff)
    add("check_schedule_all_ok", c_check_schedule_ok)
    add("late_handoff_flow", c_late_handoff_flow)
    add("dismiss_late_and_release", c_dismiss_and_release)

    globals().update(saved)
    globals()["post"] = saved_fns["post"]
    globals()["reachable"] = saved_fns["reachable"]
    globals()["NO_GO"] = saved_fns["NO_GO"]
    globals()["_PROC_START"] = saved_fns["_PROC_START"]
    globals()["resources_of"] = saved_fns["resources_of"]
    globals()["snapshot_queues"] = saved_fns["snapshot_queues"]
    globals()["queue_map"] = saved_fns["queue_map"]
    globals()["schedule"] = saved_fns["schedule"]
    shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in cases if not c["ok"]]
    return {"ok": not failed, "passed": len(cases) - len(failed), "failed": len(failed), "cases": cases}


def cmd_selftest(as_json: bool = False) -> int:
    rep = selftest()
    if as_json:
        _emit(rep, as_json)
    else:
        print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"], rep["passed"] + rep["failed"]))
        for c in rep["cases"]:
            if not c["ok"]:
                print("  - %s: %s" % (c["name"], c["detail"]))
    return 0 if rep["ok"] else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes -> AUTO-MAS 发车（替代 AUTO-MAS 定时）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reconcile", action="store_true", help="对账发车（cron 用）")
    ap.add_argument("--shift", choices=["daily", "evening"])
    ap.add_argument("--queue")
    ap.add_argument("--apply", action="store_true", help="真发车（缺省 dry-run）")
    ap.add_argument("--force", action="store_true", help="忽略当天已发记录")
    ap.add_argument("--stagger", type=int, default=180, help="整班发车间隔秒（默认 180）")
    ap.add_argument("--json", action="store_true", help="机器可读输出（单行 JSON）")
    ap.add_argument("--alerts", action="store_true", help="只读：未消除发车告警台账")
    ap.add_argument("--all", action="store_true", help="配合 --alerts：连 test 自测条目一起显示")
    ap.add_argument("--clear-test-alerts", action="store_true", help="回收 --task-id 自测留下的 test 告警")
    ap.add_argument("--selftest", action="store_true", help="离线自测（假传输；不碰网络/runtime）")
    ap.add_argument("--check-schedule", action="store_true",
                    help="只读：SHIFT_TABLE ↔ AUTO-MAS 队列/时间/槽位一致性校验")
    ap.add_argument("--busy", action="store_true", help="只读：当前 busy 窗口 + 未关闭 late 标记")
    ap.add_argument("--release", metavar="QUEUE", help="清 busy 窗口（人工确认对方任务已结束；默认 dry-run）")
    ap.add_argument("--dismiss-late", metavar="QUEUE", help="关闭 late 标记（决定不补；默认 dry-run）")
    ap.add_argument("--note", default="", help="配合 --dismiss-late：理由（进审计）")
    ap.add_argument("--force-conflict", action="store_true",
                    help="显式放行资源冲突拒绝（记账 conflict_overridden，谨慎用）")
    ap.add_argument("--task-id", help="自测注入：替换 POST 的 taskId（仅 --queue；记录带 test 标记）")
    ap.add_argument("--retries", type=int, default=None, help="覆盖重试次数（默认 3）")
    ap.add_argument("--retry-delays", default=None, help="覆盖退避秒表（如 \"60,180,300\"）")
    ap.add_argument("--retry-budget", type=int, default=None, help="覆盖单进程退避总预算秒（默认 700）")
    a = ap.parse_args()
    over = {}
    if a.retries is not None:
        over["retries"] = max(0, a.retries)
    if a.retry_delays is not None:
        over["retry_delays"] = _parse_delays(a.retry_delays)
    if a.retry_budget is not None:
        over["retry_budget"] = max(0, a.retry_budget)
    if a.force_conflict:
        over["force_conflict"] = True
    if a.task_id and not a.queue:
        print("--task-id 仅与 --queue 一起用（自测注入）")
        return 2
    if a.check_schedule:
        return cmd_check_schedule(a.json)
    if a.busy:
        return cmd_busy(a.json)
    if a.release:
        return cmd_release(a.release, a.apply, a.json)
    if a.dismiss_late:
        return cmd_dismiss_late(a.dismiss_late, a.apply, a.note, a.json)
    if a.check:
        return cmd_check(a.json)
    if a.list:
        return cmd_list(a.json)
    if a.selftest:
        return cmd_selftest(a.json)
    if a.alerts:
        return cmd_alerts(a.all, a.json)
    if a.clear_test_alerts:
        return cmd_clear_test_alerts(a.json)
    if a.reconcile:
        return cmd_reconcile(a.apply, a.force, a.json, **over)
    if a.queue:
        if a.task_id:
            over.update(post_id=a.task_id, test=True)
        return cmd_queue(a.queue, a.apply, a.force, a.json, **over)
    if a.shift:
        return cmd_shift(a.shift, a.apply, a.force, a.stagger, a.json, **over)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
