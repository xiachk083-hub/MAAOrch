#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_audit.py —— 审计日志**统一入口**：一套 schema 写、一套命令查（含旧日志桥接）+ 轮转/保留。

定位（解决什么）：现在审计分散在 `mas_ledger.jsonl` / `mas_dispatch_log.jsonl` /
`mas_ondemand_log.jsonl` / `mas_run_profile_log.jsonl` / `farm_run_*.md` / kanban 注释……
**字段不一、无法统一查询**，而且会无限增长。本工具把「写」收敛到一条流（`runtime/audit/mas_audit.jsonl`，
schema = `maaorch.audit.v1`，定义见 `docs/MAS_AUDIT_SCHEMA.md`），把「查」做成**多源合并**
（统一流 + 归档 + 旧日志桥接适配器），把「长」交给**按大小/天数轮转 + 保留期**。

子命令（**append 是写；其余默认只读/dry-run**）：
    append    追加一条统一事件（其它工具/worker/人也用它；= 其它工具接入的唯一入口）
    query     查询：时间/动作/目标/actor/结果/卡号/关键字过滤；`--legacy` 桥接旧日志一起查
    status    水位：统一流 + 归档 + 旧日志各套的体积/行数/最新时间（与存储策略卡同口径）
    rotate    轮转检查（默认只报；`--apply` 才真转）—— append 时自动执行同款检查（可用 env 关闭）
    prune     归档保留期清理（**默认 dry-run 只列候选**；`--apply` 才删；人工执行）
    validate  校验（统一流 + 归档里每行是否符合 v1 schema）
    schema    打印 v1 JSON Schema（机器可读）
    --selftest  离线自测（零触网、只在临时目录里写）

统一事件（v1，见表 docs/MAS_AUDIT_SCHEMA.md §2）：
    {"v":1,"ts":"YYYY-MM-DD HH:MM:SS","actor":"tool:mas_ondemand","action":"dispatch.start",
     "target":"account:帝国猛虎","result":"ok","reason":"",            # 必填 6 项 + reason
     "params":{...},"evidence":[...],"card":"t_xxxxxxx","run_id":"...","extra":{...}}  # 空则省略

字段口径（与卡面一一对应）：ts｜actor（人/worker/工具/调度/主机）｜action（域.动词）｜
target（kind:id）｜params（**脱敏**、≤2000 字符，超了截断留指纹）｜result（ok/fail/refused/info/unknown）｜
evidence（文件/命令/URL 指针）｜reason（为什么拒绝/失败）｜card（关联看板卡）。

脱敏红线（写入前强制）：凭据类键（password/token/secret/cookie/credential/skland/phone…）**整键丢弃**；
字符串值里的 11 位手机号（`134****5276` → 只留首3+末4）；params 超长截断。
**禁止**把口令/验证码/token/cookie/全串手机号写进任何字段（包括 reason/evidence）。

用法（`$PY312` = C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe，仅 stdlib、默认 python 也能跑）：
    $PY312 tools/mas_audit.py append --actor tool:mas_ondemand --action dispatch.start \
        --target account:帝国猛虎 --result ok --card t_xxxxxxx \
        --param mode=AutoProxy --param code=200 --evidence runtime/mas_ondemand_log.jsonl \
        --reason "" [--run-id ...] [--ts "..."] [--dry-run] [--json]
    $PY312 tools/mas_audit.py query --since 2026-09-28 --action 'dispatch.*' --target 'account:*' --limit 50 --json
    $PY312 tools/mas_audit.py query --since 7d --legacy --grep 卡在 --limit 30      # 旧日志一起查（桥接）
    $PY312 tools/mas_audit.py status --json
    $PY312 tools/mas_audit.py rotate                 # 看阈值/是否该转（只读）
    $PY312 tools/mas_audit.py rotate --apply         # 真轮转（归档 → archive/*.jsonl.gz）
    $PY312 tools/mas_audit.py prune                  # 列保留期外归档（不删）
    $PY312 tools/mas_audit.py prune --apply          # 删（人工拍板后执行）
    $PY312 tools/mas_audit.py validate
    $PY312 tools/mas_audit.py schema --json
    $PY312 tools/mas_audit.py --selftest

退出码：0 成功（查询命中与否都算成功；命中数看末行 AUDIT_QUERY）；1 仅 `--json --strict-rc` 的无命中；
        2 拒绝（用法/schema 不合规/时间参数坏）；3 自检失败。数据面不可读（--legacy 某源缺/看板库锁住）
        **不改变退出码**，在 JSON 的 unreadable[] 与 stderr 警告里显式可见（fail-closed，不许当 0）。

末行判据（机检 grep）：
    AUDIT=APPENDED|DRY-RUN|ERROR:<原因>      append
    AUDIT_QUERY=<命中>条 scanned=<行> legacy=<n>   query
    AUDIT_STATUS=OK|WARN|CRIT ...            status
    AUDIT_ROTATE=NOOP|DRY-RUN|ROTATED:<name> rotate
    AUDIT_PRUNE=candidates=<n> kept=<m> apply=…  prune
    AUDIT_VALIDATE=OK|BAD:<n> ...            validate
    SELFTEST=OK|FAIL (n/n)                   --selftest

环境变量：MAAORCH_AUDIT_DIR（统一流目录，默认 runtime/audit）、
         MAAORCH_AUDIT_NO_ROTATE=1（append 时不做自动轮转）、
         MAAORCH_AUDIT_LEGACY_RUNTIME（旧日志所在 runtime，自测/异机用）、
         HERMES_KANBAN_DB（--legacy 里 board 适配器的看板库；没有就跳过）、
         HERMES_KANBAN_TASK（append 未显式给 --card 时自动带上，worker 场景少写参数）。

约束：本工具**不删任何旧日志**、不改写旧日志；`prune --apply` 只删自己 `archive/` 里的归档文件。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):                          # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)

AUDIT_DIR = os.environ.get("MAAORCH_AUDIT_DIR") or os.path.join(ROOT, "runtime", "audit")
LIVE = os.path.join(AUDIT_DIR, "mas_audit.jsonl")
ARCHIVE_DIR = os.path.join(AUDIT_DIR, "archive")
LOCK = os.path.join(AUDIT_DIR, "mas_audit.lock")
LEGACY_RUNTIME = os.environ.get("MAAORCH_AUDIT_LEGACY_RUNTIME") or os.path.join(ROOT, "runtime")

# ---------------------------------------------------------------- 常量（阈值/枚举）
SCHEMA_V = 1
TS_FMT = "%Y-%m-%d %H:%M:%S"

MAX_MB_DEFAULT = 8.0            # live 文件 ≥ 8MiB 轮转（与存储策略卡 §4.2「本机 runtime 纳入同款巡检」呼应）
MAX_DAYS_DEFAULT = 7.0          # live 文件存活 ≥ 7 天轮转（AUTO-MAS history 90 天保留的 1/13 粒度）
RETENTION_DAYS_DEFAULT = 90     # 归档保留 90 天（= 存储策略卡 §4.1 对 history 的建议保留期）
KEEP_MIN_ARCHIVES = 3           # 无论多老，至少留最新 3 份归档（防误配天数清空证据）
LOCK_STALE_S = 120              # 锁陈旧阈值（与 mas_run_profile.ledger_append 同款）
MAX_PARAM_CHARS = 2000          # params 序列化上限，超了截断留指纹
LEGACY_WARN_BYTES = 10 * 1024 * 1024   # 旧日志单文件 > 10MiB 报 WARN（status）

RESULTS = ("ok", "fail", "refused", "info", "unknown")

ACTOR_KINDS = ("human", "worker", "tool", "sched", "host")     # 人 / worker / 工具 / 调度 / 主机
ACTOR_RE = re.compile(r"^(%s):[^\s:]+$" % "|".join(ACTOR_KINDS))
ACTION_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
TARGET_RE = re.compile(r"^(-|[a-z][a-z0-9_]*:[^\s]+)$")
CARD_RE = re.compile(r"^t_[0-9a-f]{6,}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# 动作域（词表见 docs/MAS_AUDIT_SCHEMA.md §4；**未列出的域只是告警，不拒绝**——保证可扩展）
KNOWN_DOMAINS = {
    "dispatch", "profile", "farm", "stage", "account", "config", "lease", "patch",
    "board", "audit", "run", "manual", "host", "kb", "emu", "queue", "ledger",
}
KNOWN_TARGET_KINDS = {
    "account", "script", "dir", "emu", "queue", "task", "stage", "file", "host",
    "card", "run", "snapshot", "batch", "job", "-",
}

# 脱敏：凭据类键整键丢弃（与 tools/mas_run_profile.py::scrub 同表 + 手机号/会话类扩展）
BANNED_KEYS = ("password", "passwd", "token", "secret", "cookie", "credential", "skland",
               "apikey", "api_key", "private_key", "sessionid", "session_id",
               "phone", "mobile", "手机", "auth_token", "authorization")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


class AuditError(RuntimeError):
    """用法/schema/IO 拒绝（append 拒绝时调用方应当修参数，不是吞掉）。"""


# ---------------------------------------------------------------- 基础
def _now() -> str:
    return dt.datetime.now().strftime(TS_FMT)


def _now_name() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def log(msg: str) -> None:
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def mask_phone(s: str) -> str:
    """11 位手机号 → 134****5276（只留首 3 + 末 4；知识库/文档同款掩码口径）。"""
    return PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], s)


def scrub(obj):
    """递归脱敏：凭据类键丢弃 + 字符串值里手机号打码（写入前强制）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(b in str(k).lower() for b in BANNED_KEYS):
                continue                                    # 整键丢，不留半截
            out[k] = scrub(v)
        return out
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, str):
        return mask_phone(obj)
    return obj


def _sha12(obj) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _fit_params(params) -> dict:
    """params 脱敏 + 大小整形：>MAX_PARAM_CHARS 截断并留指纹（事件不丢，内容降级）。"""
    if not params:
        return {}
    p = scrub(params)
    blob = json.dumps(p, ensure_ascii=False, sort_keys=True, default=str)
    if len(blob) <= MAX_PARAM_CHARS:
        return p
    return {"_truncated": True, "_chars": len(blob), "_sha256_12": _sha12(p)}


def make_record(actor: str, action: str, target: str, result: str, *,
                params=None, reason: str = "", evidence=None, card: str = "",
                run_id: str = "", extra=None, ts: str | None = None) -> dict:
    """构造 + 校验一条 v1 事件（**不写盘**）。校验失败抛 AuditError。"""
    rec: dict = {"v": SCHEMA_V, "ts": ts or _now(), "actor": str(actor or "").strip(),
                 "action": str(action or "").strip(), "target": str(target or "").strip(),
                 "result": str(result or "").strip()}
    rec["reason"] = str(reason or "")
    if params:
        rec["params"] = _fit_params(params)
    ev = [str(x) for x in (evidence or []) if str(x).strip()]
    if ev:
        rec["evidence"] = ev
    if card:
        rec["card"] = str(card).strip()
    if run_id:
        rec["run_id"] = str(run_id).strip()
    if extra:
        rec["extra"] = scrub(extra)
    issues = validate_record(rec)
    if issues:
        raise AuditError("；".join(issues))
    return rec


def validate_record(rec: dict) -> list[str]:
    """返回问题清单（空 = 合规）。"""
    bad: list[str] = []
    if not isinstance(rec, dict):
        return ["不是 JSON 对象"]
    if rec.get("v") != SCHEMA_V:
        bad.append("v 必须是 %d" % SCHEMA_V)
    ts = str(rec.get("ts") or "")
    if not TS_RE.match(ts):
        bad.append("ts 必须是 'YYYY-MM-DD HH:MM:SS'：%r" % ts)
    actor = str(rec.get("actor") or "")
    if not ACTOR_RE.match(actor):
        bad.append("actor 必须是 human|worker|tool|sched|host:<名>：%r" % actor)
    action = str(rec.get("action") or "")
    if not ACTION_RE.match(action):
        bad.append("action 必须是 域.动词（小写，如 dispatch.start）：%r" % action)
    target = str(rec.get("target") or "")
    if not TARGET_RE.match(target):
        bad.append("target 必须是 kind:id 或 '-'：%r" % target)
    if str(rec.get("result") or "") not in RESULTS:
        bad.append("result 必须是 %s：%r" % ("/".join(RESULTS), rec.get("result")))
    if "reason" not in rec or not isinstance(rec.get("reason"), str):
        bad.append("reason 必须是字符串（可为空串）")
    if "params" in rec and not isinstance(rec["params"], dict):
        bad.append("params 必须是对象")
    if "evidence" in rec and not (isinstance(rec["evidence"], list)
                                  and all(isinstance(x, str) for x in rec["evidence"])):
        bad.append("evidence 必须是字符串数组")
    if rec.get("card") and not CARD_RE.match(str(rec["card"])):
        bad.append("card 形如 t_xxxxxxx：%r" % rec.get("card"))
    for k in ("actor", "action", "target", "reason"):
        v = rec.get(k)
        if isinstance(v, str) and PHONE_RE.search(v):
            bad.append("%s 里含未打码的 11 位手机号" % k)
    return bad


def _warn_unknown_domain(action: str) -> None:
    dom = action.split(".", 1)[0]
    if dom not in KNOWN_DOMAINS:
        print("警告：action 域名 %r 不在词表里（docs/MAS_AUDIT_SCHEMA.md §4）；"
              "如系新域请补词表。" % dom, file=sys.stderr)


# ---------------------------------------------------------------- 锁 / 写 / 轮转
@contextlib.contextmanager
def audit_lock(timeout_s: float = 30.0):
    """跨进程单飞（锁文件语义与 runtime/mas_ledger.lock 同款：陈旧 120s 可回收）。"""
    os.makedirs(AUDIT_DIR, exist_ok=True)
    deadline = time.time() + timeout_s
    got = False
    while time.time() < deadline:
        try:
            fd = open(LOCK, "x", encoding="utf-8")
            fd.write("%s pid=%s\n" % (_now(), os.getpid()))
            fd.close()
            got = True
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(LOCK) > LOCK_STALE_S:
                    os.remove(LOCK)
                    continue
            time.sleep(0.25)
        except OSError as e:                                   # 目录不可写
            raise AuditError("无法建锁 %s：%s" % (LOCK, e)) from e
    if not got:
        raise AuditError("锁超时：%s（有人持有或权限不足）" % LOCK)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(LOCK)


def _file_state(path: str) -> dict:
    try:
        st = os.stat(path)
        return {"exists": True, "bytes": st.st_size, "mtime": st.st_mtime, "path": path}
    except OSError:
        return {"exists": False, "bytes": 0, "mtime": 0.0, "path": path}


def _archive_name(when: dt.datetime | None = None) -> str:
    return "mas_audit-%s.jsonl.gz" % (when or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")


def rotate(max_mb: float = MAX_MB_DEFAULT, max_days: float = MAX_DAYS_DEFAULT,
           apply: bool = False) -> dict:
    """轮转检查：live ≥ max_mb 或存活 ≥ max_days ⇒ 归档到 archive/*.jsonl.gz（apply=True 才动）。"""
    st = _file_state(LIVE)
    out = {"apply": bool(apply), "rotate": False, "reason": "", "live": st,
           "max_mb": max_mb, "max_days": max_days, "archive": "", "archive_dir": ARCHIVE_DIR}
    if not st["exists"]:
        out["reason"] = "live 文件不存在（无需轮转）"
        return out
    size_hit = st["bytes"] >= max_mb * 1024 * 1024
    age_days = (time.time() - st["mtime"]) / 86400.0 if st["mtime"] else 0.0
    age_hit = age_days >= max_days
    out["age_days"] = round(age_days, 3)
    if not (size_hit or age_hit):
        out["reason"] = "阈值未到（size=%.1fMB<%.1f, age=%.2fd<%.1f）" % (
            st["bytes"] / 1048576.0, max_mb, age_days, max_days)
        return out
    out["rotate"] = True
    out["reason"] = "轮转：" + "、".join(
        x for x in (("体积≥%.1fMB" % max_mb) if size_hit else "",
                    ("存活≥%.1fd" % max_days) if age_hit else "") if x)
    name = _archive_name()
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    target = os.path.join(ARCHIVE_DIR, name)
    n = 1
    while os.path.exists(target):
        target = os.path.join(ARCHIVE_DIR, name[:-3] + "-%d.gz" % n)
        n += 1
    out["archive"] = target
    if not apply:
        return out
    with open(LIVE, "rb") as fi, gzip.open(target, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    os.remove(LIVE)
    out["bytes_moved"] = st["bytes"]
    return out


def append(actor: str, action: str, target: str, result: str, *,
           params=None, reason: str = "", evidence=None, card: str = "",
           run_id: str = "", extra=None, ts: str | None = None,
           path: str | None = None, do_rotate: bool | None = None) -> dict:
    """**其它工具接入点**：追加一条统一事件（脱敏 + 校验 + 加锁 + 自动轮转）。

    返回落盘的记录（dict）；校验失败抛 AuditError（调用方参数错就应当炸出来）。
    do_rotate=None ⇒ 走 env MAAORCH_AUDIT_NO_ROTATE 的缺省；False 显式跳过。
    """
    rec = make_record(actor, action, target, result, params=params, reason=reason,
                      evidence=evidence, card=card, run_id=run_id, extra=extra, ts=ts)
    _warn_unknown_domain(rec["action"])
    live = path or LIVE
    rot_dir = os.path.dirname(live) or "."
    if os.path.dirname(live) == AUDIT_DIR:                      # 只在标准目录下自动轮转
        if do_rotate is None:
            do_rotate = str(os.environ.get("MAAORCH_AUDIT_NO_ROTATE", "")).strip().lower() not in {
                "1", "true", "yes", "on"}
        with audit_lock():
            if do_rotate:
                with contextlib.suppress(AuditError):
                    rotate(apply=True)
            os.makedirs(rot_dir, exist_ok=True)
            with open(live, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    else:                                                       # 自定义路径（自测/异机）：直写
        os.makedirs(rot_dir, exist_ok=True)
        with open(live, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def safe_append(**kw) -> tuple[bool, dict]:
    """接入方不想让审计失败打断主流程时用它：返回 (ok, rec|error)。"""
    try:
        return True, append(**kw)
    except Exception as e:  # noqa: BLE001
        return False, {"error": "%s: %s" % (type(e).__name__, e)}


def stream_for_runtime(runtime_dir) -> str | None:
    """接入方 runtime 被重定向（各工具 `MAAORCH_*_RUNTIME`，自测/异机）时统一流跟随落点。

    返回 None ⇒ 走标准统一流（live + 锁 + 自动轮转）；否则返回 `<runtime>/audit/mas_audit.jsonl`
    的直写路径——`--selftest`/隔离跑不会往生产统一流灌演示事件（与各工具「自测不碰真 runtime」
    的既有约定同款；见 docs/MAS_AUDIT_SCHEMA.md §8.5）。
    """
    try:
        if os.environ.get("MAAORCH_AUDIT_DIR"):
            return None      # 显式指定统一流目录 ⇒ 优先级最高（验收/取证时想落到指定流就用它）
        if runtime_dir and os.path.abspath(str(runtime_dir)) != os.path.abspath(
                os.path.join(ROOT, "runtime")):
            return os.path.join(str(runtime_dir), "audit", "mas_audit.jsonl")
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------- 读取 / 时间
def _iter_jsonl(path: str, gz: bool = False):
    """逐行产出 (lineno, obj|None)；坏行不算错（计数交给调用方）。"""
    op = gzip.open(path, "rt", encoding="utf-8") if gz else open(path, "r", encoding="utf-8")
    with op as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except ValueError:
                yield i, None


def load_unified(include_archive: bool = True) -> dict:
    """读统一流（live + archive/*.jsonl.gz）：返回 {rows, scanned, bad, unreadable}。"""
    rows, scanned, bad, unreadable = [], 0, 0, []
    paths = []
    if os.path.exists(LIVE):
        paths.append((LIVE, False))
    if include_archive:
        for p in sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.jsonl.gz"))):
            paths.append((p, True))
    for p, gz in paths:
        try:
            for _, obj in _iter_jsonl(p, gz=gz):
                scanned += 1
                if isinstance(obj, dict):
                    obj = dict(obj)
                    obj["_origin"] = "unified:" + os.path.basename(p)
                    rows.append(obj)
                else:
                    bad += 1
        except OSError as e:
            unreadable.append("%s: %s" % (p, e))
    return {"rows": rows, "scanned": scanned, "bad": bad, "unreadable": unreadable}


# ---------------------------------------------------------------- 旧日志桥接（adapters）
def _ad_ledger(rec: dict) -> dict | None:
    """runtime/mas_ledger.jsonl → v1（kind→动作域映射；detail 是 JSON 字符串）。"""
    kind = str(rec.get("kind") or "").strip()
    if not kind:
        return None
    amap = {"run_profile": "profile.apply", "probe_farm": "farm.probe",
            "farm_orchestrator": "farm.run", "activity_switch": "account.activity_switch",
            "stage_fix": "stage.fix", "watch": "run.watch", "watch_report": "run.watch",
            "probe_stage": "stage.probe"}
    action = amap.get(kind, "ledger." + re.sub(r"[^a-z0-9_]+", "_", kind.lower()))
    detail = rec.get("detail")
    if isinstance(detail, str):
        with contextlib.suppress(ValueError):
            detail = json.loads(detail)
    reason = ""
    if isinstance(detail, dict):
        verdict = detail.get("verdict")
        reason = str(detail.get("conclusion") or detail.get("reason")
                     or (verdict.get("kind") if isinstance(verdict, dict) else verdict) or "")
    out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
           "action": action, "target": ("run:" + str(rec["run_id"])) if rec.get("run_id") else "-",
           "result": "ok" if rec.get("ok") else "fail", "reason": reason,
           "params": {"kind": kind, "scope": rec.get("scope"), "detail": detail},
           "extra": {"legacy": "mas_ledger"}}
    if rec.get("run_id"):
        out["run_id"] = str(rec["run_id"])
    return out


def _ad_dispatch(rec: dict, src: str = "mas_dispatch_log") -> dict | None:
    """dispatch/ondemand 事件流 → v1。库里有**三代形态**，逐一桥接（认不出就跳过，不编）：
       ① 班次发车记录（无 kind，有 queue/resp）：mas_dispatch_log 09-27 代
       ② ondemand 全量调用行（tool + kind=queue|script，含干跑/被拒）：mas_ondemand_log 主体
       ③ 事件行（kind=dispatch_ok|dispatch_refused|stop_ok|temp_queue_created|release…）
    """
    kind = str(rec.get("kind") or "").strip()
    tool = str(rec.get("tool") or "").strip()

    # ① 班次发车记录（无 kind/tool，有 queue + resp）
    if not kind and not tool:
        queue = rec.get("queue")
        resp = rec.get("resp")
        if not isinstance(resp, dict):
            resp = {}
        if not queue and (rec.get("script") or rec.get("dir")):
            # ④ 手工/卡片脚本发车行（script+dir+resp，无 queue）：如 t_564593ae gap_fill_q4
            ok = rec.get("ok")
            if ok is None:
                ok = (rec.get("http") or resp.get("code")) == 200
            params = {k: rec.get(k) for k in ("script", "dir", "mode", "http", "note", "source")
                      if rec.get(k) not in (None, "")}
            if resp.get("code") is not None:
                params["code"] = resp["code"]
            m = re.search(r"t_[0-9a-f]{6,}", str(rec.get("source") or ""))
            out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
                   "action": "dispatch.start",
                   "target": ("dir:%s" % rec["dir"]) if rec.get("dir") else "script:%s" % rec["script"],
                   "result": "ok" if ok else "fail",
                   "reason": str(rec.get("note") or resp.get("message") or ""), "params": params,
                   "extra": {"legacy": src, "shape": "manual-send"}}
            if m:
                out["card"] = m.group(0)
            if rec.get("taskId") or resp.get("taskId"):
                out["run_id"] = str(rec.get("taskId") or resp["taskId"])
            return out
        if not queue:
            return None
        params = {k: rec.get(k) for k in ("mode", "queue_id", "note", "source")
                  if rec.get(k) not in (None, "")}
        if resp.get("code") is not None:
            params["code"] = resp["code"]
        out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
               "action": "dispatch.start", "target": "queue:%s" % queue,
               "result": "ok" if rec.get("ok") else "fail",
               "reason": str(resp.get("message") or ""), "params": params,
               "extra": {"legacy": src, "shape": "shift-send"}}
        if rec.get("taskId"):
            out["run_id"] = str(rec["taskId"])
        return out

    # ② ondemand 全量调用行（有 tool 且 kind 是目标类型或缺失；kind=结果型的走 ③）
    if tool and (not kind or kind in ("queue", "script")):
        verb = re.sub(r"^dispatch_", "", re.sub(r"[^a-z0-9_]+", "_", tool.lower()))
        verb = {"stop_task": "stop", "ondemand_preflight": "preflight"}.get(verb, verb)
        sub = rec.get("result")
        if not isinstance(sub, dict):
            sub = {}
        verdict = str(rec.get("verdict") or "")
        blob = (verdict + " " + tool).lower()
        if any(w in blob for w in ("refus", "unknown", "denied", "reject")):
            res = "refused"
        elif rec.get("apply") is False or sub.get("dry_run"):
            res = "info"
        elif sub.get("ok") is True or rec.get("ok") is True:
            res = "ok"
        elif sub.get("ok") is False or rec.get("ok") is False:
            res = "fail"
        else:
            res = "info"
        if kind in ("queue", "script") and rec.get("target"):
            target = "%s:%s" % (kind, rec.get("target"))
        elif rec.get("target"):
            target = "task:%s" % rec.get("target")
        elif rec.get("task_id"):
            target = "task:%s" % rec.get("task_id")
        else:
            target = "-"
        params = {k: rec.get(k) for k in ("apply", "verdict", "target_uid", "task_id", "pid")
                  if rec.get(k) not in (None, "")}
        params["dry_run"] = bool(sub.get("dry_run"))
        out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
               "action": "dispatch." + verb, "target": target, "result": res,
               "reason": verdict, "params": params,
               "extra": {"legacy": src, "shape": "ondemand-call"}}
        return out

    if not kind:
        return None
    # ③ 事件行
    if "refused" in kind:
        action, result = "dispatch.refuse", "refused"
    elif kind.startswith("stop"):
        action, result = "dispatch.stop", ("ok" if rec.get("ok") else "fail")
    elif kind.startswith("dispatch"):
        action, result = "dispatch.start", ("ok" if rec.get("ok") else "fail")
    elif kind.startswith("defer"):
        action, result = "dispatch.defer", "info"
    else:
        action, result = "dispatch." + re.sub(r"[^a-z0-9_]+", "_", kind.lower()), (
            "ok" if rec.get("ok") else "fail")
    tkind = str(rec.get("target_kind") or "").strip() or "task"
    target = "%s:%s" % (tkind, rec.get("target")) if rec.get("target") else "-"
    params = {k: rec.get(k) for k in ("mode", "code", "taskId", "apply", "force", "preflight",
                                      "source", "src", "resources", "detail") if rec.get(k) not in (None, "")}
    detail = params.pop("detail", "")
    out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
           "action": action, "target": target, "result": result,
           "reason": str(rec.get("reason") or detail or ""),
           "params": params, "extra": {"legacy": src, "shape": "event"}}
    if rec.get("taskId"):
        out["run_id"] = str(rec["taskId"])
    return out


def _ad_profile(rec: dict) -> dict | None:
    ev = str(rec.get("event") or "").strip()
    if not ev:
        return None
    amap = {"plan_dry_run": "profile.plan", "plan_refused": "profile.refuse",
            "apply_start": "profile.apply", "apply_done": "profile.apply",
            "readback": "profile.readback", "restore": "profile.restore", "done": "profile.done"}
    action = amap.get(ev, "profile." + re.sub(r"[^a-z0-9_]+", "_", ev.lower()))
    result = "refused" if "refus" in ev else ("info" if "dry" in ev else "ok")
    params = {k: rec.get(k) for k in ("profile", "script", "accounts", "apply", "refusals",
                                      "mode", "rc") if rec.get(k) not in (None, "")}
    out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
           "action": action, "target": ("script:" + str(rec["script"])) if rec.get("script") else "-",
           "result": result, "reason": "", "params": params, "extra": {"legacy": "mas_run_profile_log"}}
    return out


def _ad_probe(rec: dict) -> dict | None:
    ev = str(rec.get("event") or "").strip()
    if not ev:
        return None
    amap = {"done": "farm.done", "ledger_update": "stage.ledger_update", "refuse": "farm.refuse"}
    action = amap.get(ev, "farm." + re.sub(r"[^a-z0-9_]+", "_", ev.lower()))
    rc = rec.get("rc")
    result = "ok" if rc == 0 else ("info" if rc is None else "fail")
    params = {k: rec.get(k) for k in ("stage", "before", "after", "rc", "verdict", "farm_rc")
              if rec.get(k) not in (None, "")}
    out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
           "action": action,
           "target": ("account:" + str(rec["account"])) if rec.get("account") else "-",
           "result": result, "reason": str(rec.get("verdict") or ""), "params": params,
           "extra": {"legacy": "mas_probe_farm_log"}}
    if rec.get("run_id"):
        out["run_id"] = str(rec["run_id"])
    return out


def _ad_farm(rec: dict) -> dict | None:
    ev = str(rec.get("event") or "").strip()
    if not ev:
        return None
    rc = rec.get("rc")
    out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
           "action": "farm." + re.sub(r"[^a-z0-9_]+", "_", ev.lower()),
           "target": ("run:" + str(rec["run_id"])) if rec.get("run_id") else "-",
           "result": "ok" if rc == 0 else "fail", "reason": str(rec.get("conclusion") or ""),
           "params": {"rc": rc}, "extra": {"legacy": "mas_farm_orchestrator_log"}}
    if rec.get("run_id"):
        out["run_id"] = str(rec["run_id"])
    return out


def _ad_plan(rec: dict) -> dict | None:
    ev = str(rec.get("event") or "").strip()
    if not ev:
        return None
    params = {k: rec.get(k) for k in ("script", "mode", "stage_plan", "json", "md", "activity_enabled")
              if rec.get(k) not in (None, "")}
    out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
           "action": "stage." + re.sub(r"[^a-z0-9_]+", "_", ev.lower()),
           "target": ("account:" + str(rec["account"])) if rec.get("account") else "-",
           "result": "ok", "reason": "", "params": params,
           "extra": {"legacy": "mas_plan_builder_log"}}
    return out


def _ad_snapshot(rec: dict) -> dict | None:
    act = str(rec.get("action") or "").strip()
    if not act:
        return None
    params = {k: rec.get(k) for k in ("label", "files", "sensitive", "store", "dir", "root",
                                      "root_kind", "ok", "failed", "skipped", "verify_bad")
              if rec.get(k) not in (None, "")}
    ok = rec.get("ok")
    result = "ok" if ok is True or isinstance(ok, int) and not isinstance(ok, bool) else (
        "fail" if ok is False else "info")
    out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
           "action": "config." + re.sub(r"[^a-z0-9_]+", "_", act.lower()),
           "target": ("snapshot:" + str(rec["snapshot"])) if rec.get("snapshot") else "-",
           "result": result, "reason": "", "params": params,
           "extra": {"legacy": "mas_config_snapshot_log"}}
    return out


def _ad_patch(rec: dict) -> dict | None:
    kind = str(rec.get("kind") or "").strip()
    if not kind:
        return None
    params = {k: rec.get(k) for k in ("patch", "backup", "sha256", "fails") if rec.get(k) not in (None, "")}
    out = {"v": SCHEMA_V, "ts": str(rec.get("ts") or ""), "actor": "tool:legacy",
           "action": "patch." + re.sub(r"[^a-z0-9_]+", "_", kind.lower()),
           "target": ("file:" + str(rec["file"])) if rec.get("file") else "-",
           "result": "ok" if rec.get("ok") else "fail", "reason": "",
           "params": params, "extra": {"legacy": "mas_patch_guard_log"}}
    return out


def _board_rows(db_path: str, limit: int = 2000) -> list[dict]:
    """看板库（只读）：task_events + task_comments → v1 行。库不在/锁住 ⇒ 抛异常由调用方记账。"""
    rows: list[dict] = []
    con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True, timeout=5)
    try:
        cur = con.cursor()
        cur.execute("SELECT id, task_id, kind, payload, created_at FROM task_events "
                    "ORDER BY id DESC LIMIT ?", (limit,))
        for _id, task_id, kind, payload, created in cur.fetchall():
            with contextlib.suppress(ValueError):
                payload = json.loads(payload) if payload else {}
            ts = dt.datetime.fromtimestamp(int(created)).strftime(TS_FMT)
            kmap = {"done": "ok", "completed": "ok", "crashed": "fail", "failed": "fail",
                    "timed_out": "fail", "blocked": "refused", "request_changes": "refused"}
            rows.append({"v": SCHEMA_V, "ts": ts, "actor": "tool:board",
                         "action": "board." + re.sub(r"[^a-z0-9_]+", "_", str(kind).lower()),
                         "target": "card:" + str(task_id), "result": kmap.get(str(kind), "info"),
                         "reason": "", "params": payload if isinstance(payload, dict) else {},
                         "card": str(task_id), "extra": {"legacy": "kanban.task_events", "event_id": _id}})
        cur.execute("SELECT id, task_id, author, body, created_at FROM task_comments "
                    "ORDER BY id DESC LIMIT ?", (limit,))
        for _id, task_id, author, body, created in cur.fetchall():
            ts = dt.datetime.fromtimestamp(int(created)).strftime(TS_FMT)
            rows.append({"v": SCHEMA_V, "ts": ts, "actor": "tool:board", "action": "board.comment",
                         "target": "card:" + str(task_id), "result": "info",
                         "reason": str(body or "")[:400],
                         "params": {"author": author}, "card": str(task_id),
                         "extra": {"legacy": "kanban.task_comments", "comment_id": _id}})
    finally:
        con.close()
    return rows


# 桥接源表：名字 → (说明, 读取函数)。board 是 sqlite 特例。
LEGACY_FILES = {
    "ledger": ("mas_ledger.jsonl", _ad_ledger),
    "dispatch": ("mas_dispatch_log.jsonl", _ad_dispatch),
    "ondemand": ("mas_ondemand_log.jsonl", _ad_dispatch),
    "profile": ("mas_run_profile_log.jsonl", _ad_profile),
    "probe": ("mas_probe_farm_log.jsonl", _ad_probe),
    "farm": ("mas_farm_orchestrator_log.jsonl", _ad_farm),
    "plan": ("mas_plan_builder_log.jsonl", _ad_plan),
    "snapshot": ("mas_config_snapshot_log.jsonl", _ad_snapshot),
    "patch": ("mas_patch_guard_log.jsonl", _ad_patch),
}
LEGACY_NAMES = list(LEGACY_FILES) + ["board"]


def load_legacy(which: list[str] | None = None, *, runtime: str | None = None,
                board_db: str | None = None) -> dict:
    """桥接读旧日志（**只读**）：返回 {rows, scanned, skipped, unreadable, per_source}。"""
    which = which or LEGACY_NAMES
    runtime = runtime or LEGACY_RUNTIME
    rows, scanned, skipped, unreadable, per = [], 0, 0, [], {}
    ts_bad = 0
    for name in which:
        if name == "board":
            db = board_db or os.environ.get("HERMES_KANBAN_DB") or ""
            if not db or not os.path.exists(db):
                per[name] = {"rows": 0, "scanned": 0, "note": "看板库不可用（HERMES_KANBAN_DB 未设/不存在）"}
                unreadable.append("board: 看板库不可用")
                continue
            try:
                br = _board_rows(db)
            except Exception as e:  # noqa: BLE001  sqlite 被写锁/损坏都不该拖垮查询
                per[name] = {"rows": 0, "scanned": 0, "note": "读库失败：%s" % e}
                unreadable.append("board: %s" % e)
                continue
            for r in br:
                r["_origin"] = "legacy:kanban"
            rows.extend(br)
            per[name] = {"rows": len(br), "scanned": len(br),
                         "note": "task_events+task_comments（近 %d 条）" % 2000}
            continue
        fname, fn = LEGACY_FILES[name]
        path = os.path.join(runtime, fname)
        if not os.path.exists(path):
            per[name] = {"rows": 0, "scanned": 0, "note": "文件不存在：%s" % path}
            continue
        n_in = n_out = 0
        try:
            for _, obj in _iter_jsonl(path):
                scanned += 1
                n_in += 1
                if not isinstance(obj, dict):
                    skipped += 1
                    continue
                try:
                    out = fn(obj)
                except Exception:  # noqa: BLE001  适配器永远不该拖垮查询
                    out = None
                if not out:
                    skipped += 1
                    continue
                out["ts"] = _norm_ts(out.get("ts"))
                if not TS_RE.match(str(out.get("ts") or "")):
                    ts_bad += 1                       # 时间戳认不出来：如实计数，不冒充
                out["_origin"] = "legacy:" + fname
                rows.append(out)
                n_out += 1
        except OSError as e:
            unreadable.append("%s: %s" % (path, e))
            per[name] = {"rows": 0, "scanned": 0, "note": "读失败：%s" % e}
            continue
        per[name] = {"rows": n_out, "scanned": n_in, "note": path}
    return {"rows": rows, "scanned": scanned, "skipped": skipped, "ts_bad": ts_bad,
            "unreadable": unreadable, "per_source": per}


# ---------------------------------------------------------------- 时间过滤
_REL_RE = re.compile(r"^(\d+)([mhd])$", re.I)


def parse_time(s: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """支持：'2026-09-28'、'2026-09-28 09:30[:00]'、'09:30[:00]'（今天）、'30m|12h|7d'（相对）。"""
    now = now or dt.datetime.now()
    s = str(s or "").strip()
    if not s:
        return None
    m = _REL_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"m": dt.timedelta(minutes=n), "h": dt.timedelta(hours=n), "d": dt.timedelta(days=n)}[unit]
        return now - delta
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        with contextlib.suppress(ValueError):
            d = dt.datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d":
                return d.replace(hour=0, minute=0, second=0)
            return d
    for fmt in ("%H:%M:%S", "%H:%M"):
        with contextlib.suppress(ValueError):
            t = dt.datetime.strptime(s, fmt).time()
            return now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
    return None


def _row_dt(rec: dict) -> dt.datetime | None:
    ts = str(rec.get("ts") or "")
    if not TS_RE.match(ts):
        return None
    with contextlib.suppress(ValueError):
        return dt.datetime.strptime(ts, TS_FMT)
    return None


# 旧日志时间戳形状不一（空格 / T / 分钟缺秒 / 带毫秒）——桥接时统一，解析不了的**原样返回**（不编时间）
_TS_IN = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
          "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M:%S")


def _norm_ts(s) -> str:
    t = str(s or "").strip()
    if TS_RE.match(t):
        return t
    for fmt in _TS_IN:
        with contextlib.suppress(ValueError):
            return dt.datetime.strptime(t, fmt).strftime(TS_FMT)
    return t


def _glob_match(value: str, pattern: str) -> bool:
    if not pattern or pattern == "*":
        return True
    parts = [p.strip() for p in str(pattern).split(",") if p.strip()]
    for p in parts:
        rx = "^" + re.escape(p).replace(r"\*", ".*").replace(r"\?", ".") + "$"
        if re.match(rx, value):
            return True
    return False


def query(*, since=None, until=None, action=None, target=None, actor=None, result=None,
          card=None, grep=None, limit=50, newest_first=True, include_archive=True,
          legacy=False, runtime=None, board_db=None, include_origin=True) -> dict:
    """多源合并查询：统一流（+归档）± 旧日志桥接。返回 {rows, counts...}（已过滤/已排序）。"""
    merged = load_unified(include_archive=include_archive)
    rows = merged["rows"]
    counts = {"unified": len(rows), "legacy": 0, "scanned": merged["scanned"],
              "bad": merged["bad"], "legacy_scanned": 0, "legacy_skipped": 0}
    unreadable = list(merged["unreadable"])
    per_source = {}
    if legacy:
        if legacy is True or legacy == "all":
            which = list(LEGACY_NAMES)
        else:
            which = [x.strip() for x in str(legacy).split(",") if x.strip()]
            unknown = [x for x in which if x not in LEGACY_NAMES]
            if unknown:
                raise AuditError("--legacy 不认识的源：%s（可选 %s）"
                                 % (",".join(unknown), ",".join(LEGACY_NAMES)))
        lg = load_legacy(which, runtime=runtime, board_db=board_db)
        rows += lg["rows"]
        counts["legacy"] = len(lg["rows"])
        counts["legacy_scanned"] = lg["scanned"]
        counts["legacy_skipped"] = lg["skipped"]
        counts["legacy_ts_bad"] = lg["ts_bad"]
        unreadable += lg["unreadable"]
        per_source = lg["per_source"]

    t0 = parse_time(since) if since else None
    t1 = parse_time(until) if until else None
    for label, raw, val in (("--since", since, t0), ("--until", until, t1)):
        if raw and val is None:
            raise AuditError("时间参数无法解析：%s %r（支持 日期 / '日期 时间' / HH:MM / 30m|12h|7d）"
                             % (label, raw))
    g = str(grep or "").lower()
    out, bad_ts = [], 0
    for r in rows:
        dt_r = _row_dt(r)
        if (t0 or t1) and dt_r is None:
            bad_ts += 1
            continue
        if t0 and dt_r < t0:
            continue
        if t1 and dt_r > t1:
            continue
        if action and not _glob_match(str(r.get("action") or ""), action):
            continue
        if target and not _glob_match(str(r.get("target") or ""), target):
            continue
        if actor and not _glob_match(str(r.get("actor") or ""), actor):
            continue
        if result and not _glob_match(str(r.get("result") or ""), result):
            continue
        if card and str(r.get("card") or "") != str(card):
            continue
        if g and g not in json.dumps(r, ensure_ascii=False).lower():
            continue
        if not include_origin:
            r = {k: v for k, v in r.items() if k != "_origin"}
        out.append(r)
    out.sort(key=lambda r: (str(r.get("ts") or ""), str(r.get("_origin") or "")), reverse=bool(newest_first))
    if limit and limit > 0:
        out = out[:limit]
    return {"rows": out, "count": len(out), "candidates": len(rows), "filtered_bad_ts": bad_ts,
            "counts": counts, "unreadable": unreadable, "per_source": per_source}


# ---------------------------------------------------------------- 保留期清理
def _archive_age_days(path: str) -> float:
    m = re.search(r"mas_audit-(\d{8})-(\d{6})", os.path.basename(path))
    if m:
        with contextlib.suppress(ValueError):
            t = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").timestamp()
            return (time.time() - t) / 86400.0
    with contextlib.suppress(OSError):
        return (time.time() - os.path.getmtime(path)) / 86400.0
    return 0.0


def prune(days: float = RETENTION_DAYS_DEFAULT, apply: bool = False,
          keep_min: int = KEEP_MIN_ARCHIVES) -> dict:
    """归档保留期清理：>days 天且不在「最新 keep_min 份」里的归档 ⇒ 候选（apply 才删）。"""
    files = sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.jsonl.gz")))
    keep = set(files[-int(keep_min):]) if keep_min and int(keep_min) > 0 else set()
    cand, kept = [], []
    for p in files:
        age = _archive_age_days(p)
        item = {"path": p, "age_days": round(age, 2), "bytes": _file_state(p)["bytes"]}
        (kept if (p in keep or age <= days) else cand).append(item)
    deleted = []
    if apply and cand:
        for it in cand:
            with contextlib.suppress(OSError):
                os.remove(it["path"])
                deleted.append(it["path"])
    return {"days": days, "apply": bool(apply), "keep_min": int(keep_min), "candidates": cand,
            "kept": kept, "deleted": deleted, "archive_dir": ARCHIVE_DIR, "total": len(files)}


# ---------------------------------------------------------------- 水位/校验/schema
def status(runtime: str | None = None, board_db: str | None = None) -> dict:
    """统一流 + 归档 + 旧日志各套的水位（只读；与存储策略卡同口径）。"""
    runtime = runtime or LEGACY_RUNTIME
    live = _file_state(LIVE)
    archives = []
    for p in sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.jsonl.gz"))):
        st = _file_state(p)
        archives.append({"name": os.path.basename(p), "bytes": st["bytes"],
                         "age_days": round(_archive_age_days(p), 2)})
    legacy = []
    for name, (fname, _fn) in LEGACY_FILES.items():
        p = os.path.join(runtime, fname)
        st = _file_state(p)
        rows = None
        if st["exists"]:
            with contextlib.suppress(OSError):
                rows = sum(1 for _ in open(p, "rb"))
        legacy.append({"name": name, "file": fname, "exists": st["exists"], "bytes": st["bytes"],
                       "lines": rows, "warn": st["bytes"] > LEGACY_WARN_BYTES})
    db = board_db or os.environ.get("HERMES_KANBAN_DB") or ""
    board = {"db": db, "exists": bool(db and os.path.exists(db)),
             "bytes": _file_state(db)["bytes"] if db else 0}
    rot = rotate(apply=False)
    total = live["bytes"] + sum(a["bytes"] for a in archives)
    verdict = "OK"
    if any(l["warn"] for l in legacy) or live["bytes"] > LEGACY_WARN_BYTES:
        verdict = "WARN"
    return {"verdict": verdict, "live": live, "archives": archives, "legacy": legacy,
            "board": board, "rotate_now": rot, "total_bytes": total,
            "limits": {"max_mb": MAX_MB_DEFAULT, "max_days": MAX_DAYS_DEFAULT,
                       "retention_days": RETENTION_DAYS_DEFAULT,
                       "keep_min_archives": KEEP_MIN_ARCHIVES}}


def validate(paths: list[str] | None = None) -> dict:
    """校验统一流/归档每行是否符合 v1（返回 {files, lines, bad:[{file,line,issues}]}）。"""
    if not paths:
        paths = ([LIVE] if os.path.exists(LIVE) else []) + sorted(
            glob.glob(os.path.join(ARCHIVE_DIR, "*.jsonl.gz")))
    bad, lines, files = [], 0, []
    for p in paths:
        if not os.path.exists(p):
            continue
        files.append(p)
        gz = p.endswith(".gz")
        try:
            for i, obj in _iter_jsonl(p, gz=gz):
                lines += 1
                issues = validate_record(obj) if isinstance(obj, dict) else ["不是 JSON 对象"]
                if issues:
                    bad.append({"file": os.path.basename(p), "line": i, "issues": issues})
        except OSError as e:
            bad.append({"file": os.path.basename(p), "line": 0, "issues": ["读失败：%s" % e]})
    return {"files": files, "lines": lines, "bad": bad, "ok": not bad}


def json_schema() -> dict:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "maaorch.audit.v1",
        "description": "MAAOrch 统一审计事件（docs/MAS_AUDIT_SCHEMA.md）",
        "type": "object",
        "required": ["v", "ts", "actor", "action", "target", "result", "reason"],
        "additionalProperties": False,
        "properties": {
            "v": {"const": SCHEMA_V},
            "ts": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$",
                   "description": "本地时间（CST），与仓库其它日志同格式"},
            "actor": {"type": "string",
                      "pattern": "^(human|worker|tool|sched|host):[^\\s:]+$",
                      "examples": ["tool:mas_ondemand", "worker:mas", "human:xiach", "sched:cron:2600f6415f16"]},
            "action": {"type": "string", "pattern": "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]+)+$",
                       "examples": ["dispatch.start", "profile.refuse", "audit.rotate"]},
            "target": {"type": "string", "pattern": "^(-|[a-z][a-z0-9_]*:[^\\s]+)$",
                       "examples": ["account:帝国猛虎", "dir:E:\\MAA-Multi\\MAA_6", "card:t_d937f659", "-"]},
            "result": {"enum": list(RESULTS)},
            "reason": {"type": "string", "description": "为什么拒绝/失败；无则空串"},
            "params": {"type": "object", "description": "脱敏后的输入摘要（≤2000 字符，超长截断留指纹）"},
            "evidence": {"type": "array", "items": {"type": "string"},
                         "description": "证据指针：文件路径/命令/URL（不放内容）"},
            "card": {"type": "string", "pattern": "^t_[0-9a-f]{6,}$", "description": "关联看板卡"},
            "run_id": {"type": "string"},
            "extra": {"type": "object"},
        },
    }


# ---------------------------------------------------------------- CLI
def _print_rows(rows: list[dict], limit_note: bool = True) -> None:
    for r in rows:
        origin = str(r.get("_origin") or "unified")
        tail = []
        if r.get("card"):
            tail.append("卡=%s" % r["card"])
        if r.get("run_id"):
            tail.append("run=%s" % str(r["run_id"])[:28])
        tail.append(origin.split(":", 1)[-1] if origin.startswith("legacy:") else "audit")
        reason = str(r.get("reason") or "").strip().replace("\n", " ")
        if reason:
            tail.append("原因=%s" % (reason[:80] + ("…" if len(reason) > 80 else "")))
        print("%s  %-7s %-26s %-22s %-28s %s" % (
            str(r.get("ts") or "?"), str(r.get("result") or "?"), str(r.get("actor") or "?")[:26],
            str(r.get("action") or "?")[:22], str(r.get("target") or "?")[:28], " ".join(tail)))


def cmd_append(a) -> int:
    params = {}
    for kv in (a.param or []):
        if "=" not in kv:
            print("拒绝：--param 必须是 k=v（收到 %r）" % kv, file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        params[k.strip()] = v
    card = a.card or os.environ.get("HERMES_KANBAN_TASK", "")
    try:
        rec = make_record(a.actor, a.action, a.target, a.result, params=params, reason=a.reason,
                          evidence=a.evidence, card=card, run_id=a.run_id, ts=a.ts)
    except AuditError as e:
        print("拒绝：%s" % e, file=sys.stderr)
        return 2
    _warn_unknown_domain(rec["action"])
    if a.dry_run:
        print(json.dumps(rec, ensure_ascii=False) if a.json else "".join(
            "%s: %s\n" % (k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
            for k, v in rec.items()).rstrip())
        print("AUDIT=DRY-RUN action=%s target=%s result=%s" % (rec["action"], rec["target"], rec["result"]))
        return 0
    try:
        out = append(a.actor, a.action, a.target, a.result, params=params, reason=a.reason,
                     evidence=a.evidence, card=card, run_id=a.run_id, ts=a.ts)
    except AuditError as e:
        print("AUDIT=ERROR:%s" % e, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("已追加到 %s" % LIVE)
        print(json.dumps(out, ensure_ascii=False))
    print("AUDIT=APPENDED path=%s ts=%s action=%s target=%s result=%s" % (
        LIVE, out["ts"], out["action"], out["target"], out["result"]))
    return 0


def cmd_query(a) -> int:
    q = query(since=a.since, until=a.until, action=a.action, target=a.target, actor=a.actor,
              result=a.result, card=a.card, grep=a.grep, limit=a.limit,
              newest_first=not a.oldest_first, include_archive=not a.no_archive,
              legacy=(a.legacy if a.legacy else False), board_db=a.board_db)
    if a.json:
        print(json.dumps({"schema": "maaorch.audit.query/1", "count": q["count"],
                          "candidates": q["candidates"], "counts": q["counts"],
                          "unreadable": q["unreadable"], "per_source": q["per_source"],
                          "rows": q["rows"]}, ensure_ascii=False))
    else:
        if q["per_source"]:
            print("源：", ", ".join("%s=%d" % (k, v["rows"]) for k, v in q["per_source"].items()) or "-")
        _print_rows(q["rows"])
    if q["unreadable"]:
        print("警告：有数据面读不到（fail-closed，别当 0）：%s" % "；".join(q["unreadable"]),
              file=sys.stderr)
    print("AUDIT_QUERY=%d条 scanned=%d legacy=%d" % (q["count"], q["counts"]["scanned"], q["counts"]["legacy"]))
    if a.json and a.strict_rc:
        return 0 if q["count"] else 1
    return 0


def cmd_status(a) -> int:
    st = status(board_db=a.board_db)
    if a.json:
        print(json.dumps(st, ensure_ascii=False))
    else:
        print("统一流：%s（%d bytes）｜归档 %d 份（%d bytes）｜合计 %.1f KiB" % (
            st["live"]["path"], st["live"]["bytes"], len(st["archives"]),
            sum(x["bytes"] for x in st["archives"]), st["total_bytes"] / 1024.0))
        for x in st["archives"]:
            print("  归档 %s  %6d B  %.1fd" % (x["name"], x["bytes"], x["age_days"]))
        print("旧日志（未迁移，--legacy 可桥接查询）：")
        for x in st["legacy"]:
            print("  %-9s %-32s %s %9d B  %s%s" % (
                x["name"], x["file"], "存在" if x["exists"] else "缺失", x["bytes"],
                ("%s 行" % x["lines"]) if x["lines"] is not None else "", "  ← WARN 超 10MiB" if x["warn"] else ""))
        print("看板库：%s（%s）" % (st["board"]["db"] or "未配置",
                                  "存在 %.1f MiB" % (st["board"]["bytes"] / 1048576.0)
                                  if st["board"]["exists"] else "不可用"))
        r = st["rotate_now"]
        print("轮转阈值：%.1fMB / %.1f 天；本次判定：%s" % (r["max_mb"], r["max_days"], r["reason"]))
        print("保留期：%d 天（prune 默认；至少留最新 %d 份归档）" % (
            st["limits"]["retention_days"], st["limits"]["keep_min_archives"]))
    print("AUDIT_STATUS=%s live=%dB archives=%d legacy=%d" % (
        st["verdict"], st["live"]["bytes"], len(st["archives"]), len(st["legacy"])))
    return 0


def cmd_rotate(a) -> int:
    out = rotate(max_mb=a.max_mb, max_days=a.max_days, apply=a.apply)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("判定：%s" % out["reason"])
        if out.get("archive"):
            print("目标归档：%s" % out["archive"])
    if not out["rotate"]:
        print("AUDIT_ROTATE=NOOP")
    elif out["apply"]:
        print("AUDIT_ROTATE=ROTATED:%s" % os.path.basename(out["archive"]))
    else:
        print("AUDIT_ROTATE=DRY-RUN")
    return 0


def cmd_prune(a) -> int:
    out = prune(days=a.days, apply=a.apply, keep_min=a.keep_min)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("归档目录：%s（共 %d 份）" % (out["archive_dir"], out["total"]))
        for c in out["candidates"]:
            print("  候选删除 %s  %.1f 天  %d B" % (os.path.basename(c["path"]), c["age_days"], c["bytes"]))
        for k in out["kept"]:
            print("  保留     %s  %.1f 天  %d B" % (os.path.basename(k["path"]), k["age_days"], k["bytes"]))
        if out["candidates"] and not out["apply"]:
            print("（dry-run：未删除。人工确认后加 --apply；本清单同时抄进 docs/MAS_AUDIT_SCHEMA.md §9）")
    print("AUDIT_PRUNE=candidates=%d kept=%d apply=%s" % (
        len(out["candidates"]), len(out["kept"]), str(bool(out["apply"])).lower()))
    return 0


def cmd_validate(a) -> int:
    out = validate(a.file or None)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("校验 %d 个文件 / %d 行" % (len(out["files"]), out["lines"]))
        for b in out["bad"][:50]:
            print("  BAD %s:%s %s" % (b["file"], b["line"], "; ".join(b["issues"])))
    print("AUDIT_VALIDATE=%s files=%d lines=%d bad=%d" % (
        "OK" if out["ok"] else "BAD:%d" % len(out["bad"]), len(out["files"]), out["lines"], len(out["bad"])))
    return 0 if out["ok"] else 1


def cmd_schema(a) -> int:
    sc = json_schema()
    if a.json:
        print(json.dumps(sc, ensure_ascii=False))
    else:
        print(json.dumps(sc, ensure_ascii=False, indent=1))
    print("AUDIT_SCHEMA=v%d required=%d props=%d" % (
        SCHEMA_V, len(sc["required"]), len(sc["properties"])))
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON 输出")
    common.add_argument("--selftest", action="store_true", help="离线自测（零触网，只在临时目录写）")
    ap = argparse.ArgumentParser(
        description="审计日志统一入口：append/query/status/rotate/prune/validate/schema（旧日志 --legacy 桥接）",
        parents=[common])
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("append", parents=[common], help="追加一条统一事件（其它工具接入点）")
    p1.add_argument("--actor", required=True, help="human:|worker:|tool:|sched:|host: <名>")
    p1.add_argument("--action", required=True, help="域.动词（如 dispatch.start）")
    p1.add_argument("--target", required=True, help="kind:id（如 account:甲 / dir:E:\\MAA-Multi\\MAA_6 / -）")
    p1.add_argument("--result", required=True, choices=list(RESULTS))
    p1.add_argument("--reason", default="", help="为什么拒绝/失败（可为空）")
    p1.add_argument("--param", action="append", help="键=值（可多给；脱敏后进 params）")
    p1.add_argument("--evidence", action="append", help="证据指针：文件/命令/URL（可多给）")
    p1.add_argument("--card", default="", help="关联看板卡（缺省读 HERMES_KANBAN_TASK）")
    p1.add_argument("--run-id", dest="run_id", default="", help="工具内 run_id/taskId")
    p1.add_argument("--ts", default=None, help="覆盖时间戳（补录/自测；仍须合法格式）")
    p1.add_argument("--dry-run", action="store_true", help="只打印不落盘")

    p2 = sub.add_parser("query", parents=[common], help="查询（统一流 + 归档 ± 旧日志桥接）")
    p2.add_argument("--since", help="起点：日期/时间/HH:MM/相对 30m|12h|7d")
    p2.add_argument("--until", help="终点（同 --since 格式）")
    p2.add_argument("--action", help="动作过滤（支持 * 通配与逗号列表：dispatch.*,audit.*）")
    p2.add_argument("--target", help="目标过滤（account:甲 / account:* / *:MAA_6）")
    p2.add_argument("--actor", help="actor 过滤（tool:mas_* / worker:*）")
    p2.add_argument("--result", help="结果过滤（ok,fail,refused,info,unknown）")
    p2.add_argument("--card", help="关联卡精确匹配")
    p2.add_argument("--grep", help="整行子串（大小写不敏感）")
    p2.add_argument("--limit", type=int, default=50, help="最多返回（0=全部；默认 50，新→旧）")
    p2.add_argument("--oldest-first", action="store_true", help="改为旧→新")
    p2.add_argument("--no-archive", action="store_true", help="只查 live，不含归档")
    p2.add_argument("--legacy", nargs="?", const="all", metavar="源",
                    help="桥接旧日志：不带值=全部（%s）；或写单个源名" % ",".join(LEGACY_NAMES))
    p2.add_argument("--board-db", dest="board_db", help="看板库路径（缺省用 HERMES_KANBAN_DB）")
    p2.add_argument("--strict-rc", action="store_true", help="--json 且无命中时 rc=1（脚本判据）")

    p3 = sub.add_parser("status", parents=[common], help="水位：统一流 + 归档 + 旧日志（只读）")
    p3.add_argument("--board-db", dest="board_db", help="看板库路径（缺省用 HERMES_KANBAN_DB）")
    p4 = sub.add_parser("rotate", parents=[common], help="轮转检查/执行（默认只报）")
    p4.add_argument("--apply", action="store_true", help="真轮转（归档到 archive/*.jsonl.gz）")
    p4.add_argument("--max-mb", type=float, default=MAX_MB_DEFAULT)
    p4.add_argument("--max-days", type=float, default=MAX_DAYS_DEFAULT)
    p5 = sub.add_parser("prune", parents=[common], help="归档保留期清理（默认 dry-run）")
    p5.add_argument("--days", type=float, default=RETENTION_DAYS_DEFAULT)
    p5.add_argument("--keep-min", type=int, default=KEEP_MIN_ARCHIVES)
    p5.add_argument("--apply", action="store_true", help="真删除（人工拍板后）")
    p6 = sub.add_parser("validate", parents=[common], help="校验统一流/归档是否符合 v1")
    p6.add_argument("--file", action="append", help="指定文件（可多给；默认 live+归档）")
    sub.add_parser("schema", parents=[common], help="打印 v1 JSON Schema")
    return ap


# ---------------------------------------------------------------- selftest
def selftest() -> dict:
    """离线自测：只在临时目录里写（零触网、不碰生产 runtime）。"""
    cases = []

    def case(name, fn):
        try:
            fn()
            cases.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e)})

    tmp = tempfile.mkdtemp(prefix="mas_audit_selftest_")
    # 自测全程把模块路径重定向到临时目录（**绝不碰生产 runtime/audit**）
    global AUDIT_DIR, LIVE, ARCHIVE_DIR, LOCK
    _saved = (AUDIT_DIR, LIVE, ARCHIVE_DIR, LOCK)
    AUDIT_DIR = os.path.join(tmp, "audit")
    LIVE = os.path.join(AUDIT_DIR, "mas_audit.jsonl")
    ARCHIVE_DIR = os.path.join(AUDIT_DIR, "archive")
    LOCK = os.path.join(AUDIT_DIR, "mas_audit.lock")
    env_bak = {k: os.environ.get(k) for k in ("MAAORCH_AUDIT_DIR", "MAAORCH_AUDIT_NO_ROTATE")}

    def chk(cond, msg=None):
        if not cond:
            raise AssertionError(repr(msg) if msg is not None else "条件不成立")

    def t_append_read():
        rec = append("tool:mas_audit_selftest", "audit.append", "file:" + LIVE, "ok",
                     params={"k": "v"}, card="t_d937f659", run_id="r1", path=LIVE)
        chk(rec["v"] == 1 and rec["result"] == "ok", rec)
        rows = [json.loads(x) for x in open(LIVE, encoding="utf-8")]
        chk(len(rows) == 1 and rows[0]["action"] == "audit.append", rows)
        chk(rows[0]["card"] == "t_d937f659" and rows[0]["run_id"] == "r1", rows[0])

    def t_scrub_keys():
        rec = make_record("tool:x", "audit.append", "-", "ok",
                          params={"password": "hunter2", "token": "abc", "keep": "v"})
        chk(rec.get("params") == {"keep": "v"}, rec)

    def t_mask_phone():
        rec = make_record("tool:x", "audit.append", "-", "ok",
                          params={"note": "联系 13812345678 处理"})
        chk(rec["params"]["note"] == "联系 138****5678 处理", rec)
        chk(not PHONE_RE.search(json.dumps(rec, ensure_ascii=False)))

    def t_bad_action_refused():
        try:
            make_record("tool:x", "DispatchStart", "-", "ok")
            raise AssertionError("坏 action 竟然通过")
        except AuditError:
            pass
        try:
            make_record("tool:x", "dispatch", "-", "ok")           # 单段也不算合规
            raise AssertionError("单段 action 竟然通过")
        except AuditError:
            pass

    def t_bad_actor_target_result():
        for kw in ({"actor": "mas", "action": "a.b", "target": "-", "result": "ok"},
                   {"actor": "tool:x", "action": "a.b", "target": "没有冒号", "result": "ok"},
                   {"actor": "tool:x", "action": "a.b", "target": "-", "result": "success"}):
            try:
                make_record(**kw)
                raise AssertionError("非法字段竟然通过：%r" % kw)
            except AuditError:
                pass

    def t_truncate_params():
        big = {"rows": "x" * 5000}
        rec = make_record("tool:x", "audit.append", "-", "ok", params=big)
        chk(rec["params"].get("_truncated") is True and rec["params"]["_chars"] > 2000, rec["params"])

    def t_validate_catches_phone():
        bad = {"v": 1, "ts": "2026-09-28 09:00:00", "actor": "tool:x", "action": "a.b",
               "target": "-", "result": "ok", "reason": "打给 13812345678"}
        chk(any("手机号" in i for i in validate_record(bad)), validate_record(bad))
        chk(validate_record({"v": 1, "ts": "2026-09-28 09:00:00", "actor": "worker:mas",
                             "action": "board.complete", "target": "card:t_d937f659",
                             "result": "ok", "reason": ""}) == [])

    def t_lock_release():
        chk(not os.path.exists(LOCK), "自测开始前锁应为空（临时目录）")
        with audit_lock():
            chk(os.path.exists(LOCK))
        chk(not os.path.exists(LOCK), "锁未释放")

    def t_rotate_by_size():
        d = os.path.join(tmp, "rot_size")
        os.makedirs(os.path.join(d, "audit"), exist_ok=True)
        global LIVE, ARCHIVE_DIR, AUDIT_DIR, LOCK
        keep = (LIVE, ARCHIVE_DIR, AUDIT_DIR, LOCK)
        AUDIT_DIR = os.path.join(d, "audit")
        LIVE = os.path.join(AUDIT_DIR, "mas_audit.jsonl")
        ARCHIVE_DIR = os.path.join(AUDIT_DIR, "archive")
        LOCK = os.path.join(AUDIT_DIR, "mas_audit.lock")
        try:
            with open(LIVE, "w", encoding="utf-8") as f:
                f.write("x" * 900000 + "\n")
            out = rotate(max_mb=0.5, max_days=99, apply=True)
            chk(out["rotate"] and os.path.exists(out["archive"]), out)
            chk(not os.path.exists(LIVE), "live 应已被转走")
            with gzip.open(out["archive"], "rt", encoding="utf-8") as f:
                chk(len(f.read()) > 900000, "归档内容不完整")
            append("tool:x", "audit.append", "-", "ok", path=LIVE, do_rotate=False)
            chk(os.path.exists(LIVE), "追加后 live 应重建")
        finally:
            LIVE, ARCHIVE_DIR, AUDIT_DIR, LOCK = keep

    def t_rotate_by_age():
        d = os.path.join(tmp, "rot_age")
        os.makedirs(os.path.join(d, "audit"), exist_ok=True)
        global LIVE, ARCHIVE_DIR, AUDIT_DIR, LOCK
        keep = (LIVE, ARCHIVE_DIR, AUDIT_DIR, LOCK)
        AUDIT_DIR = os.path.join(d, "audit")
        LIVE = os.path.join(AUDIT_DIR, "mas_audit.jsonl")
        ARCHIVE_DIR = os.path.join(AUDIT_DIR, "archive")
        LOCK = os.path.join(AUDIT_DIR, "mas_audit.lock")
        try:
            with open(LIVE, "w", encoding="utf-8") as f:
                f.write("{}\n")
            old = time.time() - 30 * 86400
            os.utime(LIVE, (old, old))
            out = rotate(max_mb=99, max_days=7, apply=False)
            chk(out["rotate"] and not out.get("bytes_moved"), out)     # dry-run 不搬
            out = rotate(max_mb=99, max_days=7, apply=True)
            chk(out["rotate"] and os.path.exists(out["archive"]), out)
        finally:
            LIVE, ARCHIVE_DIR, AUDIT_DIR, LOCK = keep

    def t_prune_keep_min_and_days():
        d = os.path.join(tmp, "prune")
        arch = os.path.join(d, "archive")
        os.makedirs(arch, exist_ok=True)
        global ARCHIVE_DIR
        keep = ARCHIVE_DIR
        ARCHIVE_DIR = arch
        try:
            names = ["mas_audit-20260101-000001.jsonl.gz", "mas_audit-20260201-000001.jsonl.gz",
                     "mas_audit-20260301-000001.jsonl.gz", "mas_audit-20260927-000001.jsonl.gz",
                     "mas_audit-20260928-000001.jsonl.gz"]
            for n in names:
                with gzip.open(os.path.join(arch, n), "wt", encoding="utf-8") as f:
                    f.write("{}\n")
            out = prune(days=90, apply=False)
            got = [os.path.basename(c["path"]) for c in out["candidates"]]
            chk(got == ["mas_audit-20260101-000001.jsonl.gz", "mas_audit-20260201-000001.jsonl.gz"], got)
            chk(all(os.path.exists(os.path.join(arch, n)) for n in names), "dry-run 不该删")
            out = prune(days=90, apply=True)
            chk(len(out["deleted"]) == 2 and len(glob.glob(os.path.join(arch, "*.gz"))) == 3, out)
            out = prune(days=0, apply=True, keep_min=3)                 # keep_min 兜底：最新 3 份永不动
            chk(len(out["deleted"]) == 0 and len(glob.glob(os.path.join(arch, "*.gz"))) == 3, out)
        finally:
            ARCHIVE_DIR = keep

    def t_query_filters_and_shape():
        d = os.path.join(tmp, "q")
        live_q = os.path.join(d, "mas_audit.jsonl")
        os.makedirs(os.path.dirname(live_q), exist_ok=True)
        today = dt.date.today().strftime("%Y-%m-%d")          # 相对断言随当天走（不写死日期）
        recs = [(today + " 09:00:00", "dispatch.start", "account:甲", "ok"),
                (today + " 09:10:00", "dispatch.refuse", "account:乙", "refused"),
                (today + " 10:00:00", "profile.apply", "script:S", "ok")]
        with open(live_q, "w", encoding="utf-8") as f:
            for ts, act, tgt, res in recs:
                f.write(json.dumps(make_record("tool:x", act, tgt, res, ts=ts), ensure_ascii=False) + "\n")
        global LIVE
        keep = LIVE
        LIVE = live_q
        try:
            q = query(limit=0, include_archive=False)
            chk(q["count"] == 3, q["count"])
            q = query(action="dispatch.*", limit=0, include_archive=False)
            chk(q["count"] == 2, q["count"])
            q = query(action="dispatch.refuse", limit=0, include_archive=False)
            chk(q["count"] == 1, q["count"])
            q = query(target="account:甲", limit=0, include_archive=False)
            chk(q["count"] == 1, q["count"])
            q = query(result="ok,refused", limit=0, include_archive=False)
            chk(q["count"] == 3, q["count"])
            q = query(since=today + " 09:30", limit=0, include_archive=False)
            chk(q["count"] == 1 and q["rows"][0]["action"] == "profile.apply", q["count"])
            q = query(until="09:30", limit=0, include_archive=False)
            chk(q["count"] == 2, q["count"])
            q = query(limit=1, include_archive=False)
            chk(q["count"] == 1 and q["rows"][0]["ts"] == today + " 10:00:00", q["rows"])
            q = query(limit=1, newest_first=False, include_archive=False)
            chk(q["rows"][0]["ts"] == today + " 09:00:00", q["rows"])
            q = query(grep="拒绝", limit=0, include_archive=False)
            chk(q["count"] == 0, q["count"])
            q = query(grep="dispatch.refuse", limit=0, include_archive=False)
            chk(q["count"] == 1, q["count"])
            try:                                              # 坏时间参数拒绝（不是静默不过滤）
                query(since="不是时间", limit=0, include_archive=False)
                raise AssertionError("坏 --since 竟然通过")
            except AuditError:
                pass
        finally:
            LIVE = keep

    def t_legacy_adapters():
        d = os.path.join(tmp, "legacy")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "mas_ledger.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "probe_farm", "ok": False, "run_id": "pf_1",
                                "scope": "甲 × 1-7", "detail": json.dumps({"conclusion": "卡在试探"}),
                                "ts": "2026-09-28 08:58:21"}, ensure_ascii=False) + "\n")
            f.write("坏行不是JSON\n")
            f.write(json.dumps({"kind": "stage_fix", "ok": True, "scope": "甲 × CE-6", "detail": "{}",
                                "ts": "2026-09-27T06:00:00"}, ensure_ascii=False) + "\n")  # ISO-T 形态
        with open(os.path.join(d, "mas_dispatch_log.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-09-28 07:33:57", "kind": "dispatch_ok", "target_kind": "wake",
                                "target": "明日方舟-官-2-4-47", "mode": "ManualReview", "ok": True,
                                "code": 200, "taskId": "550fc698-4ce0", "reason": "", "source": "ondemand",
                                "src": "cli"}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"ts": "2026-09-28 09:21:00", "kind": "dispatch_refused", "target_kind": "script",
                                "target": "明日方舟-官-1-1-7", "mode": "AutoProxy", "ok": False,
                                "reason": "slot_busy", "detail": "G3:目标目录正在跑 MAA"}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"ts": "2026-09-27 05:00:16", "queue": "日常定时1", "queue_id": "613f6094",
                                "mode": "AutoProxy", "ok": True, "taskId": "2fa2175e",
                                "resp": {"code": 200, "status": "success", "message": "操作成功"}},
                               ensure_ascii=False) + "\n")           # ① 班次发车记录（旧代）
            f.write(json.dumps({"ts": "2026-09-28 02:06:29", "source": "t_564593ae gap_fill_q4",
                                "script": "9bf9c95b-1877", "dir": "MAA_4", "mode": "AutoProxy",
                                "http": 200, "resp": {"code": 200, "status": "success",
                                                      "message": "操作成功", "taskId": "d573ca81"},
                                "note": "q4-1 睡大鸡蛋(官-31)"}, ensure_ascii=False) + "\n")  # ④ 手工发车
        with open(os.path.join(d, "mas_ondemand_log.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"tool": "dispatch_queue", "target": "日常定时1", "kind": "queue",
                                "apply": False, "verdict": "shift_delegated", "target_uid": "613f6094",
                                "result": {"ok": True, "dry_run": True}, "ts": "2026-09-28 05:54:34",
                                "pid": 26636}, ensure_ascii=False) + "\n")   # ② ondemand 调用行（干跑）
            f.write(json.dumps({"tool": "dispatch_script", "target": "明日方舟-官-12", "kind": "script",
                                "apply": True, "verdict": "ok", "result": {"ok": True},
                                "ts": "2026-09-28 06:01:00"}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"tool": "stop_task", "task_id": "00000000-0000-0000-0000-000000000000",
                                "apply": True, "verdict": "unknown_task_uid", "ts": "2026-09-28 05:56:10",
                                "pid": 34100}, ensure_ascii=False) + "\n")   # ② 无 kind 的调用行
        with open(os.path.join(d, "mas_run_profile_log.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "plan_refused", "apply": False, "script": "S1",
                                "refusals": [{"gate": "G2"}], "ts": "2026-09-28 09:27:41"},
                               ensure_ascii=False) + "\n")
        lg = load_legacy(["ledger", "dispatch", "ondemand", "profile"], runtime=d)
        chk(lg["scanned"] == 11 and lg["skipped"] == 1 and lg["ts_bad"] == 0, lg)
        acts = sorted(r["action"] for r in lg["rows"])
        chk(acts == ["dispatch.queue", "dispatch.refuse", "dispatch.script", "dispatch.start",
                     "dispatch.start", "dispatch.start", "dispatch.stop", "farm.probe",
                     "profile.refuse", "stage.fix"], acts)
        led = [r for r in lg["rows"] if r["action"] == "farm.probe"][0]
        chk(led["result"] == "fail" and led["target"] == "run:pf_1" and led["reason"] == "卡在试探", led)
        dis = [r for r in lg["rows"] if r["action"] == "dispatch.refuse"][0]
        chk(dis["result"] == "refused" and dis["reason"] == "slot_busy"
            and dis["target"] == "script:明日方舟-官-1-1-7", dis)
        dis_ok = [r for r in lg["rows"] if r["action"] == "dispatch.start"
                  and r["params"].get("mode") == "ManualReview"][0]
        chk(dis_ok["run_id"] == "550fc698-4ce0", dis_ok)
        shift = [r for r in lg["rows"] if r["action"] == "dispatch.start"
                 and r["params"].get("mode") == "AutoProxy"][0]          # ① 旧代班次发车
        chk(shift["target"] == "queue:日常定时1" and shift["result"] == "ok"
            and shift["reason"] == "操作成功" and shift["run_id"] == "2fa2175e"
            and shift["extra"]["shape"] == "shift-send", shift)
        od = [r for r in lg["rows"] if r["action"] == "dispatch.queue"][0]   # ② ondemand 干跑行
        chk(od["target"] == "queue:日常定时1" and od["result"] == "info"
            and od["reason"] == "shift_delegated" and od["params"]["dry_run"] is True, od)
        od2 = [r for r in lg["rows"] if r["action"] == "dispatch.script"][0]
        chk(od2["result"] == "ok" and od2["target"] == "script:明日方舟-官-12", od2)
        od3 = [r for r in lg["rows"] if r["action"] == "dispatch.stop"][0]   # 无 kind 的调用行
        chk(od3["result"] == "refused" and od3["reason"] == "unknown_task_uid"
            and od3["target"].startswith("task:"), od3)
        man = [r for r in lg["rows"] if r["extra"].get("shape") == "manual-send"][0]  # ④ 手工发车
        chk(man["target"] == "dir:MAA_4" and man["result"] == "ok" and man["card"] == "t_564593ae"
            and man["run_id"] == "d573ca81", man)
        sfix = [r for r in lg["rows"] if r["action"] == "stage.fix"][0]      # ISO-T → 空格形态
        chk(sfix["ts"] == "2026-09-27 06:00:00", sfix)
        for r in lg["rows"]:
            issues = validate_record(r)
            chk(not issues, (r, issues))
        chk(lg["per_source"]["ledger"]["rows"] == 2, lg["per_source"])

    def t_legacy_board_adapter():
        db = os.path.join(tmp, "board.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER,"
                    " kind TEXT, payload TEXT, created_at INTEGER)")
        con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT,"
                    " body TEXT, created_at INTEGER)")
        con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                    ("t_d937f659", "done", json.dumps({"summary": "ok"}), int(time.time())))
        con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                    ("t_d937f659", "mas", "统一 schema 交付", int(time.time())))
        con.commit()
        con.close()
        lg = load_legacy(["board"], runtime=tmp, board_db=db)
        chk(len(lg["rows"]) == 2, lg)
        ev = [r for r in lg["rows"] if r["action"] == "board.done"][0]
        chk(ev["result"] == "ok" and ev["target"] == "card:t_d937f659" and ev["card"] == "t_d937f659", ev)
        cm = [r for r in lg["rows"] if r["action"] == "board.comment"][0]
        chk(cm["result"] == "info" and "统一 schema" in cm["reason"], cm)
        for r in lg["rows"]:
            chk(not validate_record(r), (r, validate_record(r)))

    def t_unreadable_is_failclosed():
        lg = load_legacy(["ledger"], runtime=os.path.join(tmp, "nope"))
        chk(lg["rows"] == [] and "不存在" in lg["per_source"]["ledger"]["note"], lg)
        lg = load_legacy(["board"], runtime=tmp, board_db=os.path.join(tmp, "no-such.db"))
        chk(lg["rows"] == [] and lg["unreadable"], lg)

    def t_status_and_validate_dirs():
        st = status(runtime=os.path.join(tmp, "legacy"))
        chk("verdict" in st and st["limits"]["retention_days"] == RETENTION_DAYS_DEFAULT, st)
        chk(any(l["name"] == "ledger" and l["exists"] for l in st["legacy"]), st["legacy"])
        out = validate([os.path.join(tmp, "legacy", "mas_ledger.jsonl")])
        chk(any(True for _ in out["files"]) and out["lines"] >= 1, out)

    def t_schema_selfconsistent():
        sc = json_schema()
        sample = make_record("tool:x", "audit.append", "card:t_d937f659", "ok", ts="2026-09-28 09:00:00")
        for k, v in sample.items():
            chk(k in sc["properties"], k)
        chk(set(sc["required"]) <= set(sample), sc["required"])
        chk(sc["properties"]["result"]["enum"] == list(RESULTS))

    def t_time_parse():
        chk(parse_time("2026-09-28").strftime("%H:%M") == "00:00")
        chk(parse_time("2026-09-28 09:30").strftime("%H:%M") == "09:30")
        now = dt.datetime(2026, 9, 28, 12, 0, 0)
        chk(parse_time("30m", now=now) == now - dt.timedelta(minutes=30))
        chk(parse_time("2d", now=now) == now - dt.timedelta(days=2))
        chk(parse_time("09:15", now=now).strftime("%H:%M") == "09:15")
        chk(parse_time("不是时间") is None)

    def t_norm_ts():
        chk(_norm_ts("2026-09-28T01:14:38") == "2026-09-28 01:14:38")
        chk(_norm_ts("2026-09-28T01:14") == "2026-09-28 01:14:00")
        chk(_norm_ts("2026-09-28 01:14:38.123456") == "2026-09-28 01:14:38")
        chk(_norm_ts("2026-09-28 01:14:38") == "2026-09-28 01:14:38")
        chk(_norm_ts("2026-09-24T20:4x") == "2026-09-24T20:4x")     # 认不出 ⇒ 原样（不编时间）

    case("append 落盘并读回", t_append_read)
    case("凭据类键丢弃", t_scrub_keys)
    case("手机号打码", t_mask_phone)
    case("非法 action 拒绝", t_bad_action_refused)
    case("非法 actor/target/result 拒绝", t_bad_actor_target_result)
    case("params 超长截断留指纹", t_truncate_params)
    case("validate 抓到未打码手机号", t_validate_catches_phone)
    case("锁建立与释放", t_lock_release)
    case("按体积轮转（apply）", t_rotate_by_size)
    case("按天数轮转（dry→apply）", t_rotate_by_age)
    case("prune 保留期 + keep_min 兜底", t_prune_keep_min_and_days)
    case("query 过滤/排序/限量", t_query_filters_and_shape)
    case("旧日志适配器（ledger/dispatch/profile）", t_legacy_adapters)
    case("看板库适配器（events/comments）", t_legacy_board_adapter)
    case("数据面缺失 fail-closed", t_unreadable_is_failclosed)
    case("status/validate 口径", t_status_and_validate_dirs)
    case("schema 与样例自洽", t_schema_selfconsistent)
    case("时间解析（绝对/相对）", t_time_parse)
    case("旧日志时间戳归一（ISO-T/缺秒/毫秒）", t_norm_ts)
    AUDIT_DIR, LIVE, ARCHIVE_DIR, LOCK = _saved                 # 还原模块路径（自测结束）
    for k, v in env_bak.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for c in cases if c["ok"])
    return {"ok": passed == len(cases), "passed": passed, "failed": len(cases) - passed, "cases": cases}


def main(argv=None) -> int:
    ap = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(argv)
    # 子 parser 的默认值会盖掉「写在子命令前面」的同名全局开关（argparse 已知坑）⇒ 显式回升
    if "--json" in argv and not a.json:
        a.json = True
    if "--selftest" in argv and not a.selftest:
        a.selftest = True
    if a.selftest:
        rep = selftest()
        if a.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"],
                                           rep["passed"] + rep["failed"]))
            for c in rep["cases"]:
                print("  %s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                                     "" if c["ok"] else "  ← " + c["detail"]))
        return 0 if rep["ok"] else 3
    if not a.cmd:
        ap.print_help()
        return 2
    fn = {"append": cmd_append, "query": cmd_query, "status": cmd_status, "rotate": cmd_rotate,
          "prune": cmd_prune, "validate": cmd_validate, "schema": cmd_schema}[a.cmd]
    try:
        return fn(a)
    except AuditError as e:
        print("拒绝：%s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
