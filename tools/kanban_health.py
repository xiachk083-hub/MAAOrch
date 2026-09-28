#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""看板治理体检（只读）—— 重复卡 / 僵尸卡 / 长期 blocked / 崩溃重派 / 孤儿产物 的自动检测与建议。

背景：mas + dsh 两块板已 130+ 张卡（09-27 峰值 80 卡并发 31），人工肉眼盯不过来。
本工具把「运维手册里靠人看」的四类病 + 一类数据完整性问题做成可复跑的检测，**只出清单和建议，
绝不自动归档 / 重派 / 改卡**（处置权归 lead，见 docs/KANBAN_DISPATCH_POLICY.md §4/§6）。

数据源（全部只读，直接开 sqlite `mode=ro`，不起 Hermes、不连网）：
  C:\Users\xiach\AppData\Local\hermes\kanban\boards\<slug>\kanban.db
    tasks / task_runs / task_events / task_comments / task_attachments / task_links
  ± `--boards-root` 覆盖；`--board <slug>` 只扫指定板。

检测项（阈值见 §阈值，全部可用 CLI 覆盖）：
  ① **重复/近重复卡**   标题归一化后 difflib 相似度 ≥ `--dup-ratio`（默认 0.86）成对；同 `idempotency_key`
                        的非归档卡视为硬重复；已归档的重复只登记不告警（那是历史收口，别再来回翻）。
  ② **僵尸卡**          running 卡超 `--zombie-h`（默认 4h）且心跳断 `--hb-gap-min`（默认 60min）
                        —— 与内核 `detect_stale_running` 同判据（4h + 1h 心跳断 ⇒ 自动回收）；
                        另报「从未有心跳」「claim 过期未回收（调度器可能停摆）」「活着的长跑卡」。
  ③ **长期 blocked**    blocked 超 `--blocked-h`（默认 6h）且期间无新注释；`dependency` 且父卡全 done
                        的僵持单独报（内核本该自动放行）。
  ④ **崩溃/超限**       `gave_up`（failure_limit 落地未解）、`consecutive_failures ≥ 2`、
                        `timed_out`（迭代预算 150 轮 / max_runtime）、同卡 ≥2 次 crashed、
                        `block_loop_detected` / `completion_blocked_empty_result` / 协议违规。
  ⑤ **孤儿产物**        done/review/archived 卡声明的产物路径（run metadata `artifacts` + 摘要里的
                        绝对/仓库相对路径 + 附件）在盘上不存在；scratch 工作区清理掉的、以及已有
                        同名附件兜住的，都按预期排除（只计真正的“说了没落盘”）。

产出（默认写盘，`--no-write` 关掉）：
  runtime/reports/kanban_health_<ts>.md    人读报告：逐类清单 + 每条的**建议动作**（归档/重派/补注释/合并）
  runtime/reports/kanban_health_<ts>.json  机读版（schema maaorch.kanban_health/1，供以后接巡检）

用法：
  python tools/kanban_health.py                      # 扫全部板 → 写 md+json → 打印摘要 + 末行 KANBAN_HEALTH=
  python tools/kanban_health.py --board maa-parallel-benchmark-20260926
  python tools/kanban_health.py --line               # 只打一行 KANBAN_HEALTH=（巡检取数用）
  python tools/kanban_health.py --json               # 单行 JSON（含 line 字段，不写盘）
  python tools/kanban_health.py --no-write           # 不写盘，终端看摘要
  python tools/kanban_health.py --selftest           # 纯本地自检（合成 DB，不碰真板）
选项：--boards-root P --board S --zombie-h H --hb-gap-min M --blocked-h H --dup-ratio R
      --long-run-min M --board-cap N --fail-limit N --batch-window-min M --extra-root P --limit N --ts TS
退出码：0 全绿；2 有 P2/P3；3 有 P1（需处置）；1 用法/内部错误；4 一块板都没扫到

口径细节（防误报，实测校准）：
  * 产物路径核验五态：exists / staged（同名附件兜住）/ cleaned（scratch 工作区完成即清理）/ 
    remote（本机无此盘，如远端主机的 E:\AUTO-MAS —— 不核验、不计孤儿）/ missing（真缺，才报）；
    另展开 `foo.json/.md` 这类「基名+多扩展名」缩写；glob/占位/省略号 token 直接丢弃。
  * 相对路径解析根：本仓库 + 相邻工程（MAS-KB / arknights-community）+ 该板 board.json 的 default_workdir
    + --extra-root（模块间互相引用产物路径很常见）。
  * 批量崩溃聚合：同窗口（--batch-window-min，默认 20min）同错误家族 ≥3 卡 ⇒ 合一条板级项，
    成员卡降为 P3 留痕（否则 09-28 09:40 那种 18 卡同因崩溃会刷 18 条 P2 噪声）。
  * `no_artifact`（done 但零产出证据）要求附件/artifacts/交付物键/summary/result **全空**才报。

与巡检的接线（以后，不在本工具内做）：任何有 terminal/MCP 的巡检只要跑 `--line`，把末行
`KANBAN_HEALTH=…` 原样摘进报告即可；本工具**不**改卡、不改库、不写卡（只写 runtime/reports/）。
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
from difflib import SequenceMatcher

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTS = ROOT / "runtime" / "reports"
SCHEMA = "maaorch.kanban_health/1"

# ---------------------------------------------------------------- 阈值（改这里 = 改口径，务必同步 docstring 与报告页脚）
THR = {
    "zombie_h": 4.0,        # 与内核 dispatch_stale_timeout_seconds(14400) 对齐
    "hb_gap_min": 60.0,     # 与内核 _STALE_HEARTBEAT_GAP_SECONDS(3600) 对齐
    "blocked_h": 6.0,
    "dup_ratio": 0.86,
    "long_run_min": 60.0,   # 活着的长跑卡：墙钟 ≥ 此值 ⇒ 列出来复核（手册：单卡中位 ~21min）
    "board_cap": 6,         # 板级并发建议上限（KANBAN_DISPATCH_POLICY §7 建议 max_spawn=6）
    "fail_limit": 2,        # 内核 DEFAULT_FAILURE_LIMIT
    "claim_late_min": 20.0, # claim_expires 过期这么久还没被回收 ⇒ 调度器可能停摆
    "batch_window_min": 20.0,  # 批量崩溃聚合窗口（同窗口同家族 ≥3 卡 ⇒ 一条板级项）
}

STATUS_ACTIVE = ("todo", "ready", "running", "review")
STATUS_ALIVE = ("todo", "ready", "running", "review", "blocked")
DONE_LIKE = ("done", "review", "archived")

ARTIFACT_KEYS = (
    "artifacts", "deliverable", "deliverables", "files_created", "key_files",
    "changed_files", "outputs", "output", "report", "report_path", "files",
)
EXT_RE = re.compile(r"\.(md|json|txt|py|mjs|js|ts|csv|tsv|log|png|jpe?g|svg|html|ps1|sh|bat|pptx|xlsx|pdf|ya?ml|ini)$", re.I)
# 路径 token 的终止符（中英文标点都要断，否则会把「（改：§5」吃进路径）
_PATH_STOP_CHARS = "\"'<>|，,；;、）)】]（(【［[「」『』：:"
_PATH_STOP_CLS = "".join(re.escape(ch) for ch in _PATH_STOP_CHARS) + r"\s"
PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|~[\\/])[^" + _PATH_STOP_CLS + r"]+(?:[\\/][^" + _PATH_STOP_CLS + r"]+)*"
    r"|(?<![\w./\\-])(?:docs|runtime|tools|config|logs|tests|data|queue|services|ui|app|drafts)"
    r"[\\/][^" + _PATH_STOP_CLS + r"]+"
)
# 明显不是「一个文件」的 token：glob/占位/省略号
PATH_SKIP_CHARS = set("{}*<>?")
# 相对路径的额外解析根（模块 3 里相邻工程互引很常见：MAAOrch 的卡会写 arknights-community/MAS-KB 的路径）
DEFAULT_EXTRA_ROOTS = ("MAS-KB", "arknights-community")
BLOCK_KINDS = ("dependency", "needs_input", "capability", "transient")

P1, P2, P3 = "P1", "P2", "P3"
SEV_ORDER = {P1: 0, P2: 1, P3: 2}
SEV_LABEL = {P1: "需处置", P2: "需关注", P3: "留痕"}


# ---------------------------------------------------------------- 小工具

def now_ts() -> int:
    return int(time.time())


def fmt_ts(epoch) -> str:
    if not epoch:
        return "-"
    return dt.datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M")


def age_h(epoch, now) -> float | None:
    if not epoch:
        return None
    return (now - int(epoch)) / 3600.0


def age_min(epoch, now) -> float | None:
    if not epoch:
        return None
    return (now - int(epoch)) / 60.0


def h_ago(epoch, now) -> str:
    a = age_h(epoch, now)
    if a is None:
        return "从未"
    if a < 1:
        return f"{a * 60:.0f}min前"
    if a < 48:
        return f"{a:.1f}h前"
    return f"{a / 24:.1f}d前"


def norm_title(s: str) -> str:
    """标题归一化：NFKC 由调用侧保证；去掉非字母数字/汉字，压掉空白。"""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def pid_alive(pid) -> bool | None:
    """进程是否活着（Windows：OpenProcess+GetExitCodeProcess；POSIX：kill 0）。
    None = 没 pid 可查。注意：pid 活着 ≠ 是本卡 worker（fingerprint 校验在调度器侧）。"""
    if not pid:
        return None
    pid = int(pid)
    if os.name == "nt":
        try:
            k = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return False
                return int(code.value) == STILL_ACTIVE
            finally:
                k.CloseHandle(h)
        except Exception:  # noqa: BLE001
            return None
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def expand(p: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(p).strip().strip('"').strip("'")))


def expand_variants(p: str) -> list[str]:
    """把 `foo.json/.md`（基名 + 多扩展名缩写）展开成真实候选路径；别无脑猜。

    卡面/元数据里很常见「runtime/reports/perf_check.json/.md (新增)」这种写法——
    它不是两个字面文件，而是 basename + 扩展名列表。
    """
    parts = p.replace("\\", "/").split("/")
    out: list[str] = []
    for i in range(len(parts) - 1):
        head, nxt = parts[i], parts[i + 1]
        if "." not in head or not nxt.startswith(".") or len(nxt) < 2 or "/" in nxt:
            continue
        stem = head[:head.rfind(".")]
        exts = [head[head.rfind(".") + 1:], nxt.lstrip(".")]
        j = i + 2
        while j < len(parts) and parts[j].startswith(".") and len(parts[j]) > 1:
            exts.append(parts[j].lstrip("."))
            j += 1
        prefix = "/".join(parts[:i])
        for e in dict.fromkeys(exts):
            cand = (prefix + "/" if prefix else "") + stem + "." + e
            out.append(cand)
    return out


def path_state(raw: str, workspace: str | None, attachments: dict, board_ws_root: pathlib.Path,
               bases: tuple[pathlib.Path, ...] = ()) -> tuple[str, str]:
    """返回 (state, path)：exists / staged / cleaned / remote / missing。

    remote = 本机根本不存在的根（如 E:\\ 这种远端主机的路径）⇒ 不判孤儿；
    cleaned = scratch 工作区（完成即清理，预期行为）；staged = 已有同名附件兜住。
    """
    p = expand(raw)
    if not p:
        return "missing", raw
    cands = [p]
    if not os.path.isabs(p):
        cands += [str(x / p) for x in bases]
        if workspace:
            cands.append(str(pathlib.Path(workspace) / p))
    for cand in cands:
        try:
            if os.path.exists(cand):
                return "exists", cand
        except OSError:
            pass
    # 「基名 + 多扩展名」缩写（foo.json/.md）⇒ 展开成真实候选再查一次
    for cand in expand_variants(p):
        vcands = [cand]
        if not os.path.isabs(cand):
            vcands += [str(x / cand) for x in bases]
            if workspace:
                vcands.append(str(pathlib.Path(workspace) / cand))
        for vc in vcands:
            try:
                if os.path.exists(vc):
                    return "exists", vc
            except OSError:
                pass
    # 远端主机路径（本机无此盘）—— 不可核验，不算孤儿
    drive = os.path.splitdrive(expand(raw))[0]
    if drive and not os.path.exists(drive + os.sep):
        return "remote", cands[0]
    # scratch 工作区完成时被清理 —— 预期行为，不算孤儿
    for cand in cands:
        try:
            if str(pathlib.Path(cand)).lower().startswith(str(board_ws_root).lower()):
                return "cleaned", cand
        except Exception:  # noqa: BLE001
            pass
    # 同名附件兜底（storage 化过的产物）
    base = os.path.basename(p.replace("\\", "/"))
    if base and base in attachments:
        return "staged", str(attachments[base])
    return "missing", cands[0]


# ---------------------------------------------------------------- 板读取（只读）

def find_boards(root: pathlib.Path, only: list[str] | None) -> list[tuple[str, pathlib.Path]]:
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue                      # _archived/ 整板归档，不扫
        db = d / "kanban.db"
        if not db.is_file():
            continue
        if only and d.name not in only:
            continue
        out.append((d.name, db))
    return out


def open_ro(db: pathlib.Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def table_cols(con, table: str) -> set[str]:
    try:
        return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def load_board(slug: str, db: pathlib.Path) -> dict:
    con = open_ro(db)
    try:
        have = {t: table_cols(con, t) for t in
                ("tasks", "task_runs", "task_events", "task_comments", "task_attachments", "task_links")}
        want = {
            "tasks": ["id", "title", "body", "assignee", "status", "priority", "created_by", "created_at",
                      "started_at", "completed_at", "workspace_kind", "workspace_path", "result",
                      "idempotency_key", "consecutive_failures", "worker_pid", "worker_started_at",
                      "last_failure_error", "max_runtime_seconds", "last_heartbeat_at", "current_run_id",
                      "claim_expires", "max_retries", "block_kind", "block_recurrences", "completion_contract"],
            "task_runs": ["id", "task_id", "profile", "status", "outcome", "last_heartbeat_at", "started_at",
                          "ended_at", "summary", "metadata", "error"],
            "task_events": ["id", "task_id", "run_id", "kind", "payload", "created_at"],
            "task_comments": ["id", "task_id", "author", "body", "created_at"],
            "task_attachments": ["id", "task_id", "filename", "stored_path", "size", "created_at"],
            "task_links": ["parent_id", "child_id"],
        }
        data: dict[str, list[dict]] = {}
        for t, cols in want.items():
            use = [c for c in cols if c in have.get(t, set())]
            rows = con.execute(f"select {', '.join(use)} from {t}").fetchall() if use else []
            data[t] = [{k: r[k] for k in r.keys()} for r in rows]
    finally:
        con.close()

    board_json = db.parent / "board.json"
    meta = {}
    if board_json.is_file():
        try:
            meta = json.loads(board_json.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    return {
        "slug": slug, "db": str(db), "meta": meta,
        "ws_root": db.parent / "workspaces",
        "tasks": data["tasks"], "runs": data.get("task_runs", []), "events": data.get("task_events", []),
        "comments": data.get("task_comments", []), "attachments": data.get("task_attachments", []),
        "links": data.get("task_links", []),
    }


# ---------------------------------------------------------------- 通用聚合

def index_board(b: dict) -> dict:
    idx: dict = {}
    idx["by_id"] = {t["id"]: t for t in b["tasks"]}
    runs_by_task: dict[str, list[dict]] = {}
    for r in b["runs"]:
        runs_by_task.setdefault(r["task_id"], []).append(r)
    for v in runs_by_task.values():
        v.sort(key=lambda r: (r.get("started_at") or 0, r.get("id") or 0))
    idx["runs_by_task"] = runs_by_task
    ev_by_task: dict[str, list[dict]] = {}
    for e in b["events"]:
        ev_by_task.setdefault(e["task_id"], []).append(e)
    for v in ev_by_task.values():
        v.sort(key=lambda e: (e.get("created_at") or 0, e.get("id") or 0))
    idx["ev_by_task"] = ev_by_task
    cm_by_task: dict[str, list[dict]] = {}
    for c in b["comments"]:
        cm_by_task.setdefault(c["task_id"], []).append(c)
    idx["cm_by_task"] = cm_by_task
    at_by_task: dict[str, list[dict]] = {}
    for a in b["attachments"]:
        at_by_task.setdefault(a["task_id"], []).append(a)
    idx["at_by_task"] = at_by_task
    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for l in b["links"]:
        children.setdefault(l["parent_id"], []).append(l["child_id"])
        parents.setdefault(l["child_id"], []).append(l["parent_id"])
    idx["parents"], idx["children"] = parents, children
    return idx


def payload_of(ev: dict) -> dict:
    p = ev.get("payload")
    if isinstance(p, dict):
        return p
    if isinstance(p, str):
        try:
            v = json.loads(p)
            return v if isinstance(v, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def metadata_of(run: dict) -> dict:
    md = run.get("metadata")
    if isinstance(md, dict):
        return md
    if isinstance(md, str) and md.strip():
        try:
            v = json.loads(md)
            return v if isinstance(v, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def last_ts(events: list[dict], kinds: set[str]) -> int:
    return max([0, *[int(e["created_at"] or 0) for e in events if e.get("kind") in kinds]])


def collect_paths(md: dict, text: str) -> list[tuple[str, str]]:
    """(source, path)：source = declared(元数据硬声明) / prose(摘要文本提到)。

    过滤：glob/占位符（{a,b}、*、?）、省略号截断（… / ...）、不含扩展名的 token 一律不取。
    """
    out: list[tuple[str, str]] = []

    def keep(tok: str) -> bool:
        if len(tok) < 4 or PATH_SKIP_CHARS & set(tok):
            return False
        if "..." in tok or "…" in tok:
            return False
        return bool(EXT_RE.search(tok))

    def walk(v, key_ok: bool):
        if isinstance(v, dict):
            for k, vv in v.items():
                walk(vv, key_ok or str(k).lower() in ARTIFACT_KEYS)
        elif isinstance(v, (list, tuple)):
            for vv in v:
                walk(vv, key_ok)
        elif isinstance(v, str) and key_ok and (v.strip()):
            for m in PATH_RE.finditer(v):
                if keep(m.group(0)):
                    out.append(("declared", m.group(0)))

    walk(md, False)
    if text:
        for m in PATH_RE.finditer(str(text)):
            if keep(m.group(0)):
                out.append(("prose", m.group(0)))
    # 去重保序
    seen, uniq = set(), []
    for src, p in out:
        k = (src, p)
        if k not in seen:
            seen.add(k)
            uniq.append((src, p))
    return uniq


# ---------------------------------------------------------------- ① 重复/近重复

def detect_dups(b: dict, idx: dict, thr: dict, now: int) -> list[dict]:
    out: list[dict] = []
    tasks = [t for t in b["tasks"] if t["status"] != "archived"]
    tasks.sort(key=lambda t: t.get("created_at") or 0)
    for i in range(len(tasks)):
        for j in range(i + 1, len(tasks)):
            a, c = tasks[i], tasks[j]
            na, nc = norm_title(a["title"]), norm_title(c["title"])
            if len(na) < 4 or len(nc) < 4:
                continue
            r = similar(na, nc)
            prefix = (na.startswith(nc) or nc.startswith(na)) and min(len(na), len(nc)) / max(len(na), len(nc)) >= 0.7
            if r < thr["dup_ratio"] and not prefix:
                continue
            st = {a["status"], c["status"]}
            running = [x for x in (a, c) if x["status"] == "running"]
            done_n = sum(1 for x in (a, c) if x["status"] == "done")
            if running:
                keep, code, sev, why = running[0], "dup_active", P1, "两张都在活跃车道（一在跑）⇒ 现在就在重复烧算力"
            elif st & set(STATUS_ACTIVE):
                keep, code, sev, why = a, "dup_active", P1, "两张都未收口（≥1 张在等待派发）⇒ 派发扩散前先合并"
            elif done_n == 2:
                keep, code, sev, why = a, "dup_done", P2, "两张都已 done ⇒ 一条 archive 命令即可收口（趁没人记得时做完）"
            else:
                keep, code, sev, why = a, "dup_partial", P2, "一张已收口、一张仍活跃 ⇒ 确认是否在做同一件事"
            drop = c if keep is a else a
            out.append({
                "cat": "dup", "code": code, "sev": sev,
                "board": b["slug"], "ids": [a["id"], c["id"]],
                "title": f"{a['id']}({a['status']}) ≈ {c['id']}({c['status']}) 相似度 {r:.2f}",
                "detail": f"A《{a['title']}》 / B《{c['title']}》 —— {why}（建卡 {fmt_ts(a.get('created_at'))} vs {fmt_ts(c.get('created_at'))}）",
                "action": f"保留 {keep['id']}（{keep['status']}），把 {drop['id']} 的发现/产物 comment 汇总到保留卡后 archive；若两都在跑，先 archive 后发那张止血",
                "cmd": f"hermes kanban --board {b['slug']} comment {keep['id']} \"与 {drop['id']} 重复，已收口\"  # 再 hermes kanban archive {drop['id']}",
            })
    # idempotency_key 硬重复
    bykey: dict[str, list[dict]] = {}
    for t in b["tasks"]:
        if t.get("idempotency_key") and t["status"] != "archived":
            bykey.setdefault(t["idempotency_key"], []).append(t)
    for k, ts in bykey.items():
        if len(ts) > 1:
            out.append({
                "cat": "dup", "code": "dup_idem", "sev": P2, "board": b["slug"],
                "ids": [x["id"] for x in ts],
                "title": f"同一 idempotency_key ×{len(ts)}: {k}",
                "detail": "内核应拦住同 key 重复建卡；出现多张说明有卡被改正 key 或越过 CLI 建卡",
                "action": "保留最新一张，其余按重复处理（comment + archive）",
                "cmd": "", 
            })
    return out


def resolved_dups(b: dict, thr: dict) -> list[dict]:
    """已归档的重复：只登记（避免后人再翻一遍），不告警。"""
    arch = [t for t in b["tasks"] if t["status"] == "archived"]
    live = [t for t in b["tasks"] if t["status"] != "archived"]
    out = []
    for a in arch:
        na = norm_title(a["title"])
        if len(na) < 4:
            continue
        best, best_r = None, 0.0
        for c in live:
            nc = norm_title(c["title"])
            if len(nc) < 4:
                continue
            r = similar(na, nc)
            if r > best_r:
                best, best_r = c, r
        if best is not None and best_r >= 0.9:
            out.append((a["id"], best["id"], round(best_r, 2), a["title"]))
    return out


# ---------------------------------------------------------------- ② 僵尸卡 / claim / 长跑

def detect_zombies(b: dict, idx: dict, thr: dict, now: int) -> list[dict]:
    out: list[dict] = []
    running = [t for t in b["tasks"] if t["status"] == "running"]
    for t in running:
        tid = t["id"]
        runs = idx["runs_by_task"].get(tid, [])
        cur = next((r for r in runs if r["id"] == t.get("current_run_id")), None) or (runs[-1] if runs else None)
        started = (cur or {}).get("started_at") or t.get("started_at")
        wall_h = age_h(started, now) or 0.0
        hb = t.get("last_heartbeat_at") or (cur or {}).get("last_heartbeat_at")
        hb_age = age_min(hb, now)
        pid = t.get("worker_pid")
        alive = pid_alive(pid)
        common = {
            "cat": "zombie", "board": b["slug"], "ids": [tid],
            "title": f"{tid} running {wall_h:.1f}h（心跳 {h_ago(hb, now)}，pid={pid}{'' if alive is None else (' 活' if alive else ' 死')}）",
            "detail": f"《{t['title']}》 起跑 {fmt_ts(started)}；调度器判据 = 4h + 1h 心跳断 ⇒ 自动回收",
            "cmd": f"hermes kanban --board {b['slug']} log {tid} | tail -40  # 再看 show",
        }
        ce = t.get("claim_expires")
        ce_age = age_min(ce, now) if ce else None
        if wall_h >= thr["zombie_h"] and hb_age is None:
            out.append({**common, "code": "zombie_no_hb", "sev": P1,
                        "detail": common["detail"] + "；本 run 从未有过心跳（spawn 后即挂/卡死）",
                        "action": "确认 pid 已死（日志尾行）⇒ reclaim 让内核重排；spawn 即死先查环境（解释器/路径回归）",
                        **{"cmd": f"hermes kanban --board {b['slug']} reclaim {tid}"}})
        elif wall_h >= thr["zombie_h"] and hb_age is not None and hb_age >= thr["hb_gap_min"]:
            out.append({**common, "code": "zombie", "sev": P2,
                        "action": "等下一 tick 内核自动回收（无害）；10 分钟仍 running 再查调度器/手动 reclaim",
                        "cmd": f"hermes kanban --board {b['slug']} show {tid}"})
        elif wall_h * 60 >= thr["long_run_min"] and (cur is None or (cur or {}).get("status") == "running"):
            mrs = t.get("max_runtime_seconds") or 0
            sev = P2 if (mrs and wall_h * 3600 >= 0.9 * mrs) else P3
            extra = "；已到 max_runtime 90% ⇒ 即将被 SIGTERM" if sev == P2 else "；心跳正常 ⇒ 复核是否跑飞（拆卡线：单卡中位 ~21min）"
            out.append({**common, "code": "long_run", "sev": sev,
                        "action": ("准备接收 timed_out：产物先落盘，下一轮拆卡" if sev == P2 else "看 logs/t_<id>.log 的工具调用数/空转，决定是否拆卡"),
                        "cmd": f"hermes kanban --board {b['slug']} runs {tid}"})
        if ce_age is not None and ce_age >= thr["claim_late_min"] and t["status"] == "running":
            out.append({
                "cat": "zombie", "code": "claim_stale", "sev": P1, "board": b["slug"], "ids": [tid],
                "title": f"{tid} claim 过期 {abs(ce_age):.0f}min 未回收",
                "detail": f"claim_expires={fmt_ts(ce)} 早已过期而卡仍 running ⇒ 回收循环没跑（调度器停摆 / 网关掉）",
                "action": "先查调度器活着（网关进程 + kanban dispatch tick），别急着单卡 reclaim（回收后还会被下一次 tick 重排）",
                "cmd": "hermes kanban --board %s stats" % b["slug"],
            })
    # 板级：并发
    if len(running) > thr["board_cap"]:
        out.append({
            "cat": "board", "code": "concurrency_high", "sev": P2, "board": b["slug"],
            "ids": [],
            "title": f"running {len(running)} 张 > 板级建议上限 {thr['board_cap']}",
            "detail": "KANBAN_DISPATCH_POLICY §0/§2：稳态 4–6 张/波；09-27 峰值 31 是事故不是能力。"
                      "不停正在跑的（会毁掉半成品），新卡留在 todo 即可",
            "action": "新波暂缓：把待发卡留在 todo（不 ready 就不派）；已跑的等收口",
            "cmd": f"hermes kanban --board {b['slug']} list --status running",
        })
    return out


# ---------------------------------------------------------------- ③ 长期 blocked

def detect_blocked(b: dict, idx: dict, thr: dict, now: int) -> list[dict]:
    out: list[dict] = []
    for t in b["tasks"]:
        if t["status"] != "blocked":
            continue
        tid = t["id"]
        evs = idx["ev_by_task"].get(tid, [])
        blocked_evs = [e for e in evs if e.get("kind") in ("blocked", "gave_up")]
        blocked_at = blocked_evs[-1]["created_at"] if blocked_evs else (t.get("started_at") or t.get("created_at"))
        held_h = age_h(blocked_at, now) or 0.0
        cms = idx["cm_by_task"].get(tid, [])
        last_cm = max([c["created_at"] for c in cms], default=0)
        new_cm = last_cm > (blocked_at or 0)
        kind = t.get("block_kind")
        rec = t.get("block_recurrences") or 0
        base = {
            "cat": "blocked", "board": b["slug"], "ids": [tid],
            "title": f"{tid} blocked {held_h:.1f}h（kind={kind or '未标'}，recur={rec}，最后注释 {h_ago(last_cm, now) if last_cm else '无'}）",
            "detail": f"《{t['title']}》 阻塞于 {fmt_ts(blocked_at)}；" + ("阻塞后无人补注释" if not new_cm else "阻塞后有新注释"),
            "cmd": f"hermes kanban --board {b['slug']} show {tid}",
        }
        parents = idx["parents"].get(tid, [])
        unblockable = bool(parents) and all(idx["by_id"].get(p, {}).get("status") == "done" for p in parents)
        if kind == "dependency" and unblockable and held_h >= 1.0:
            out.append({**base, "code": "blocked_dep_ready", "sev": P1,
                        "action": "父卡已全 done 但没自动放行（内核应已 promote）⇒ 手动 unblock，并在卡上注明复核时间",
                        "cmd": f"hermes kanban --board {b['slug']} unblock {tid}"})
            continue
        if held_h < thr["blocked_h"]:
            continue
        if kind in ("needs_input", "capability"):
            out.append({**base, "code": "blocked_human", "sev": P2,
                        "action": "在等人工决策：把「等谁/等什么/怎么解/复查时间」补进卡面或 comment；或给出临时放行口径",
                        "cmd": f"hermes kanban --board {b['slug']} comment {tid} \"解锁条件：…；复查：…\""})
        elif kind == "dependency":
            out.append({**base, "code": "blocked_dependency", "sev": P3,
                        "action": "等上游（父卡未 done）；若上游已废，直接 archive 本卡并把理由写进 comment",
                        "cmd": ""})
        else:
            out.append({**base, "code": "blocked_legacy_kind", "sev": P2,
                        "action": "老式 block 无 kind/无解锁条件 ⇒ 补一条 comment 说明怎么解，或重 block 带 --kind",
                        "cmd": f"hermes kanban --board {b['slug']} block {tid} --kind needs_input \"…\""})
    return out


# ---------------------------------------------------------------- ④ 崩溃/超限

def _err_family(err: str) -> str:
    s = (err or "").lower()
    if "no module named 'hermes_cli'" in s or "error while finding module specification" in s:
        return "环境·解释器（hermes_cli 导入回归）"
    if "iteration budget exhausted" in s:
        return "迭代预算（150 轮用满）"
    if "elapsed" in s and "limit" in s:
        return "max_runtime 超时"
    if "not alive" in s or "exited with code" in s:
        return "worker 退出（崩溃）"
    if "ssh" in s or "connection" in s or "timed out" in s:
        return "网络/连接类"
    return "未归类"


def detect_failures(b: dict, idx: dict, thr: dict, now: int, batched: frozenset | set = frozenset()) -> list[dict]:
    out: list[dict] = []
    for t in b["tasks"]:
        if t["status"] in ("done", "archived"):
            continue
        tid = t["id"]
        evs = idx["ev_by_task"].get(tid, [])
        runs = idx["runs_by_task"].get(tid, [])
        cmn = {"cat": "fail", "board": b["slug"], "ids": [tid]}
        reset_ts = last_ts(evs, {"completed", "unblocked", "promoted", "archived", "edited"})
        gave_up = [e for e in evs if e.get("kind") == "gave_up" and int(e["created_at"]) > reset_ts]
        if gave_up:
            pl = payload_of(gave_up[-1])
            eff = pl.get("effective_limit")
            out.append({**cmn, "code": "gave_up", "sev": P1,
                        "title": f"{tid} 自动止损（gave_up，{len(gave_up)} 次未解）{'' if not eff else f' 阈值={eff}'}",
                        "detail": f"《{t['title']}》 最近一次 {h_ago(gave_up[-1]['created_at'], now)}："
                                  f"{_err_family(str(pl.get('error', '') or t.get('last_failure_error', '')))}",
                        "action": "先修环境/卡面，再在卡上写重试备注后 promote（禁止裸 promote，见 §4.3）；同因 2 卡 → 开环境专项卡 P3",
                        "cmd": f"hermes kanban --board {b['slug']} show {tid}"})
        cf = int(t.get("consecutive_failures") or 0)
        if cf >= thr["fail_limit"] and t["status"] in STATUS_ALIVE and not gave_up:
            out.append({**cmn, "code": "limit_reached", "sev": P1,
                        "title": f"{tid} 连败 {cf} ≥ failure_limit {thr['fail_limit']}",
                        "detail": f"《{t['title']}》 last_failure_error：{_err_family(str(t.get('last_failure_error') or ''))}",
                        "action": "达到止损线：人工定性后再动（改卡面/修环境/换时间窗），不要靠反复 promote 顶",
                        "cmd": f"hermes kanban --board {b['slug']} show {tid}"})
        elif cf >= thr["fail_limit"] - 1 and cf > 0 and t["status"] in STATUS_ALIVE and not gave_up:
            in_batch = tid in batched
            out.append({**cmn, "code": "limit_hot", "sev": P3 if in_batch else P2,
                        "title": f"{tid} 已 {cf} 连败（再失败一次即 auto-block）"
                                 + ("（批量崩溃成员，见板级项）" if in_batch else ""),
                        "detail": f"《{t['title']}》 最近失败：{_err_family(str(t.get('last_failure_error') or ''))}",
                        "action": ("批量崩溃成员：随板级「批量崩溃」项一起处置；单独再失败才需在本卡补现场"
                                   if in_batch else
                                   "趁还没止损：把现场（日志尾行）写进 comment，必要时先 pause 再修，避免第二次失败白烧"),
                        "cmd": f"hermes kanban --board {b['slug']} comment {tid} \"现场：…\""})
        tos = [e for e in evs if e.get("kind") == "timed_out" and int(e["created_at"]) > reset_ts]
        if tos:
            pl = payload_of(tos[-1])
            err = str(pl.get("error") or (t.get("last_failure_error") or ""))
            budget = "iteration budget exhausted" in err.lower()
            out.append({**cmn, "code": "timed_out_budget" if budget else "timed_out_runtime", "sev": P2,
                        "title": f"{tid} 超限中断 ×{len(tos)}（{'迭代预算 150 轮' if budget else 'max_runtime'}）",
                        "detail": f"《{t['title']}》 最近 {h_ago(tos[-1]['created_at'], now)}；整卡作废重跑 = 白烧",
                        "action": ("拆卡：按对象/数据集/章节切，单卡 ≤120 轮留余量（§1.4）" if budget
                                   else "调大 max_runtime_seconds 或拆卡；产物要增量落盘，别只留在最后一次收尾"),
                        "cmd": f"hermes kanban --board {b['slug']} edit {tid} --body \"…（拆卡后的范围）\""})
        crashes = [r for r in runs if (r.get("outcome") in ("crashed", "spawn_failed")) or r.get("status") == "crashed"]
        if len(crashes) >= 2:
            last = crashes[-1]
            out.append({**cmn, "code": "crash_repeat", "sev": P2,
                        "title": f"{tid} 崩溃 ×{len(crashes)}（最近 {h_ago(last.get('ended_at') or last.get('started_at'), now)}）",
                        "detail": f"《{t['title']}》 家族：{_err_family(str(last.get('error') or t.get('last_failure_error') or ''))}",
                        "action": "同卡反复崩溃 ⇒ 先定性（环境类修环境；卡面类补判据）再重派；同因多卡升级为系统级专项",
                        "cmd": f"hermes kanban --board {b['slug']} log {tid} | tail -60"})
        for kind, code, sev, act in (
            ("block_loop_detected", "loop_detected", P2, "同因反复 block 触发防护：确认是否真能解（不能就 archive 或改卡面）"),
            ("completion_blocked_empty_result", "empty_completion", P2, "收尾时无证据被拒：卡面补一句「最后必须带 summary/artifacts 调 kanban_complete」"),
            ("protocol_violation", "protocol_violation", P2, "exit 0 但没调看板终态：卡面写死终态要求"),
        ):
            hits = [e for e in evs if e.get("kind") == kind and int(e["created_at"]) > reset_ts]
            if hits:
                out.append({**cmn, "code": code, "sev": sev,
                            "title": f"{tid} {kind} ×{len(hits)}（最近 {h_ago(hits[-1]['created_at'], now)}）",
                            "detail": f"《{t['title']}》 持卡阶段={t['status']}",
                            "action": act, "cmd": ""})
    return out


# ---------------------------------------------------------------- ⑤ 孤儿产物

def detect_orphans(b: dict, idx: dict, now: int, bases: tuple = ()) -> tuple[list[dict], dict]:
    out: list[dict] = []
    stats = {"checked": 0, "remote": 0, "cleaned": 0, "staged": 0, "exists": 0}
    for t in b["tasks"]:
        if t["status"] not in DONE_LIKE:
            continue
        tid = t["id"]
        atts = idx["at_by_task"].get(tid, [])
        by_name = {}
        for a in atts:
            by_name[os.path.basename(str(a.get("stored_path") or ""))] = a.get("stored_path")
        missing: dict[str, list[str]] = {}

        def _check(src: str, p: str):
            state, resolved = path_state(p, t.get("workspace_path"), by_name, b["ws_root"], bases)
            stats["checked"] += 1
            stats[state] = stats.get(state, 0) + 1
            if state == "missing":
                missing.setdefault(src, []).append(resolved)

        for r in idx["runs_by_task"].get(tid, []):
            md = metadata_of(r)
            if not md:
                continue
            for src, p in collect_paths(md, ""):
                _check(src, p)
        # 摘要/结果文本里的路径（弱证据）
        for r in idx["runs_by_task"].get(tid, []):
            for src, p in collect_paths({}, str(r.get("summary") or "") + "\n" + str(t.get("result") or "")):
                _check(src, p)
        if missing.get("declared"):
            paths = sorted(set(missing["declared"]))
            out.append({
                "cat": "orphan", "code": "artifact_missing", "sev": P2, "board": b["slug"], "ids": [tid],
                "title": f"{tid} 声明产物在盘上不存在 ×{len(paths)}（{t['status']}）",
                "detail": f"《{t['title']}》 缺失：{'；'.join(paths[:3])}{' …' if len(paths) > 3 else ''}",
                "action": "补产出（重跑该卡或补文件）并把路径写回卡上；若产物已在别处，把真实路径 comment 进卡",
                "cmd": f"hermes kanban --board {b['slug']} comment {tid} \"产物实际位置：…\"",
            })
        if missing.get("prose"):
            paths = sorted(set(missing["prose"]))
            out.append({
                "cat": "orphan", "code": "prose_path_missing", "sev": P3, "board": b["slug"], "ids": [tid],
                "title": f"{tid} 摘要提到但盘上无 ×{len(paths)}（弱证据）",
                "detail": f"《{t['title']}》 {'；'.join(paths[:3])}{' …' if len(paths) > 3 else ''}",
                "action": "多半是相对路径/已迁走；确认后忽略即可（若确为交付物缺失，按 artifact_missing 处置）",
                "cmd": "",
            })
        for a in atts:
            sp = a.get("stored_path")
            try:
                ok = bool(sp) and os.path.exists(str(sp))
            except OSError:
                ok = False
            if not ok:
                out.append({
                    "cat": "orphan", "code": "attachment_missing", "sev": P1, "board": b["slug"], "ids": [tid],
                    "title": f"{tid} 附件文件丢失：{a.get('filename')}",
                    "detail": f"stored_path={sp}",
                    "action": "附件是交付物本体：先找副本（备份/其他板附件目录），找不到就在卡上标注丢失并重建",
                    "cmd": "",
                })
        # done 但零证据（无附件 / 无 artifacts / 无产物类元数据键 / 无摘要 / 无 result）
        if t["status"] == "done" and not atts:
            runs = idx["runs_by_task"].get(tid, [])
            has_art = any(metadata_of(r).get("artifacts") for r in runs)
            has_keys = any(any(str(k).lower() in ARTIFACT_KEYS and str(v).strip() for k, v in metadata_of(r).items())
                           for r in runs)
            has_prose = any(str(r.get("summary") or "").strip() for r in runs)
            if not (has_art or has_keys or has_prose) and not str(t.get("result") or "").strip():
                out.append({
                    "cat": "orphan", "code": "no_artifact", "sev": P3, "board": b["slug"], "ids": [tid],
                    "title": f"{tid} done 但零产出证据（{t['title'][:24]}）",
                    "detail": "卡 done：无附件、无 artifacts、无交付物元数据键、无 summary、result 为空 ⇒ 后人无法核验产出",
                    "action": "补一句 result/comment 说明产出在哪；或把交付物补进 artifacts",
                    "cmd": "",
                })
    return out, stats


# ---------------------------------------------------------------- 批量崩溃（同窗口同家族 ≥3 卡）

def crash_batches(b: dict, idx: dict, thr: dict, now: int) -> tuple[list[dict], set[str]]:
    """把「同一时间窗内、同一错误家族」崩溃的 ≥3 张卡聚成一条板级项；返回 (findings, 成员 id 集)。

    单卡各自报 = 27 条噪声；聚合成一条 = 「09:40 一批 27 卡同一个死因」这种一眼可断的事故。
    成员卡在 ④ 里降为 P3 留痕（不重复占 P2）。
    """
    window_s = thr.get("batch_window_min", 20.0) * 60
    rows = []
    for t in b["tasks"]:
        if t["status"] in ("done", "archived"):
            continue
        runs = idx["runs_by_task"].get(t["id"], [])
        crashed = [r for r in runs if r.get("outcome") in ("crashed", "spawn_failed") or r.get("status") == "crashed"]
        if not crashed:
            continue
        last = crashed[-1]
        ts = last.get("ended_at") or last.get("started_at") or 0
        rows.append((ts, _err_family(str(last.get("error") or t.get("last_failure_error") or "")), t["id"], len(crashed)))
    rows.sort()
    out: list[dict] = []
    members: set[str] = set()
    used = [False] * len(rows)
    for i in range(len(rows)):
        if used[i]:
            continue
        grp = [rows[i]]
        used[i] = True
        for j in range(i + 1, len(rows)):
            if used[j]:
                continue
            if rows[j][1] == grp[0][1] and rows[j][0] - grp[-1][0] <= window_s:
                grp.append(rows[j])
                used[j] = True
        if len(grp) < 3:
            continue
        ids = [g[2] for g in grp]
        members.update(ids)
        out.append({
            "cat": "fail", "code": "crash_batch", "sev": P2, "board": b["slug"], "ids": ids,
            "title": f"批量崩溃 ×{len(ids)}（家族：{grp[0][1]}；{fmt_ts(grp[0][0])}–{fmt_ts(grp[-1][0])}）",
            "detail": f"同窗口 {len(ids)} 张卡同家族崩溃后被重派：{' '.join(ids[:8])}{' …' if len(ids) > 8 else ''}；"
                      f"重派前若没修死因，第二次失败就吃满 failure_limit 自动止损（=白烧一轮）",
            "action": "先按家族定性（环境类修环境；疑似调度/网关抖动看 gateway 日志），修完再放它跑；同因 ≥2 卡本就该升级系统级专项",
            "cmd": f"hermes kanban --board {b['slug']} list --status running",
        })
    return out, members


# ---------------------------------------------------------------- 汇总

def scan_board(slug: str, db: pathlib.Path, thr: dict, now: int, bases: tuple = ()) -> dict:
    b = load_board(slug, db)
    idx = index_board(b)
    findings: list[dict] = []
    findings += detect_dups(b, idx, thr, now)
    findings += detect_zombies(b, idx, thr, now)
    findings += detect_blocked(b, idx, thr, now)
    batches, batched = crash_batches(b, idx, thr, now)
    findings += detect_failures(b, idx, thr, now, batched)
    findings += batches
    orphan_findings, orphan_stats = detect_orphans(b, idx, now, bases)
    findings += orphan_findings
    for f in findings:
        f.setdefault("cmd", "")
    findings.sort(key=lambda f: (SEV_ORDER.get(f["sev"], 9), f["cat"], f["ids"]))
    by_status: dict[str, int] = {}
    for t in b["tasks"]:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    return {
        "slug": slug, "db": str(db), "name": (b["meta"] or {}).get("name") or slug,
        "cards": len(b["tasks"]), "by_status": by_status,
        "running": by_status.get("running", 0),
        "resolved_dups": resolved_dups(b, thr),
        "orphan_stats": orphan_stats,
        "findings": findings,
    }


def severity_of(findings: list[dict]) -> str:
    sevs = {f["sev"] for f in findings}
    if P1 in sevs:
        return "CRIT"
    if P2 in sevs or P3 in sevs:
        return "WARN"
    return "OK"


def compact_counts(boards: list[dict]) -> dict:
    """行内每类计数 = **需动作项**（P1/P2）；P3 留痕只进 P3= 汇总（细节见报告）。"""
    c = {"cards": 0, "running": 0, "dup": 0, "zombie": 0, "blocked_stale": 0, "fail": 0, "orphan": 0}
    for b in boards:
        c["cards"] += b["cards"]
        c["running"] += b["running"]
        for f in b["findings"]:
            if f["sev"] not in (P1, P2):
                continue
            if f["cat"] == "dup":
                c["dup"] += 1
            elif f["cat"] == "zombie" and f["code"] in ("zombie", "zombie_no_hb", "claim_stale"):
                c["zombie"] += 1
            elif f["cat"] == "blocked":
                c["blocked_stale"] += 1
            elif f["cat"] == "fail":
                c["fail"] += 1
            elif f["cat"] == "orphan" and f["code"] in ("artifact_missing", "attachment_missing"):
                c["orphan"] += 1
    return c


def build_line(boards: list[dict], totals: dict, counts: dict, status: str, ts_str: str) -> str:
    return ("KANBAN_HEALTH=ts=%s,boards=%d,cards=%d,running=%d,dup=%d,zombie=%d,blocked_stale=%d,"
            "fail=%d,orphan=%d,P1=%d,P2=%d,P3=%d,status=%s" % (
                ts_str, len(boards), counts["cards"], counts["running"], counts["dup"], counts["zombie"],
                counts["blocked_stale"], counts["fail"], counts["orphan"],
                totals[P1], totals[P2], totals[P3], status))


def build_doc(boards: list[dict], thr: dict, now: int, ts_str: str, line_ts: str | None = None) -> dict:
    findings = [f for b in boards for f in b["findings"]]
    totals = {P1: 0, P2: 0, P3: 0}
    for f in findings:
        totals[f["sev"]] = totals.get(f["sev"], 0) + 1
    counts = compact_counts(boards)
    status = severity_of(findings)
    line = build_line(boards, totals, counts, status, line_ts or ts_str)
    return {
        "schema": SCHEMA, "ts": ts_str, "epoch": now, "status": status,
        "thresholds": thr, "totals": {**totals, "findings": len(findings)}, "counts": counts,
        "boards": boards, "line": line,
    }


# ---------------------------------------------------------------- 报告（md）

CAT_TITLES = {
    "dup": "① 重复 / 近重复卡",
    "zombie": "② 僵尸卡 · claim 过期 · 长跑卡",
    "blocked": "③ 长期 blocked",
    "fail": "④ 崩溃 / 超限 / 止损",
    "orphan": "⑤ 孤儿产物（说了没落盘）",
    "board": "⑥ 板级态势",
}


def render_md(doc: dict, thr: dict, now: int, limit: int) -> str:
    L: list[str] = []
    add = L.append
    add(f"# 看板治理体检 · {doc['ts']}")
    add("")
    add(f"- 口径：只读扫 kanban.db（tasks/runs/events/comments/attachments/links）；**不归档、不重派、不改卡**。"
        f"建议动作由 lead 决定（依据 docs/KANBAN_DISPATCH_POLICY.md §4/§6）。")
    add(f"- 阈值：僵尸={thr['zombie_h']}h+心跳断{thr['hb_gap_min']:.0f}min（与内核一致）；长期 blocked={thr['blocked_h']}h；"
        f"重复相似度≥{thr['dup_ratio']}；长跑={thr['long_run_min']:.0f}min；板级并发上限={thr['board_cap']}；failure_limit={thr['fail_limit']}")
    add(f"- 结论：**{doc['status']}** — P1 {doc['totals'][P1]} / P2 {doc['totals'][P2]} / P3 {doc['totals'][P3]}"
        f"（共 {doc['totals']['findings']} 条）")
    add("")
    add("| 板 | 卡数 | running | 状态分布 | dup | zombie | blocked_stale | fail | orphan |")
    add("|---|---|---|---|---|---|---|---|---|")
    for b in doc["boards"]:
        f = b["findings"]
        n = lambda cat, codes=None: sum(1 for x in f if x["cat"] == cat and (codes is None or x["code"] in codes))  # noqa: E731
        dist = " ".join(f"{k}={v}" for k, v in sorted(b["by_status"].items()))
        add(f"| {b['slug']} | {b['cards']} | {b['running']} | {dist} | {n('dup')} | {n('zombie', ('zombie', 'zombie_no_hb'))} | "
            f"{n('blocked')} | {n('fail')} | {n('orphan', ('artifact_missing', 'attachment_missing'))} |")
    add("")
    if not doc["totals"]["findings"]:
        add("**全绿**：五类检测均无命中。")
    for cat, title in CAT_TITLES.items():
        items = [f for b in doc["boards"] for f in b["findings"] if f["cat"] == cat]
        if not items:
            add(f"## {title}")
            add("")
            add("- 无。")
            add("")
            continue
        add(f"## {title}（{len(items)}）")
        add("")
        for i, f in enumerate(items):
            if i >= limit:
                add(f"- … 其余 {len(items) - limit} 条见同名 `.json`（`--limit` 可调）")
                break
            tag = f"[{f['sev']}·{SEV_LABEL[f['sev']]}]"
            add(f"- {tag} `{f['code']}` · {f['title']}")
            add(f"    - 事实：{f['detail']}")
            add(f"    - 建议：{f['action']}")
            if f.get("cmd"):
                add(f"    - 命令：`{f['cmd']}`")
        add("")
    # 已归档重复（历史收口）
    rows = [(b["slug"], *x) for b in doc["boards"] for x in b["resolved_dups"]]
    if rows:
        add(f"## 附：已归档的重复（{len(rows)} 对，无需动作）")
        add("")
        add("> 这些是历史收口（09-27 重复波等）。列出来只为避免后人再翻一遍；不要去 archive/restore。")
        add("")
        for slug, aid, bid, r, title in rows[:limit]:
            add(f"- `{aid}`(archived) ≈ `{bid}` 相似度 {r} — 《{title}》")
        add("")
    add("---")
    add("")
    add("## 复算与接线")
    add("")
    add("- 每个数字都可用 SQL 复算（见 JSON 的 `boards[].cards/by_status`；僵尸判据 = tasks.started_at/current_run 与 last_heartbeat_at）。")
    add("- 产物路径核验口径（每轮统计见 JSON `boards[].orphan_stats`）：`exists` 在盘上、`staged` 有同名附件兜住、"
        "`cleaned` 落在已清理的 scratch 工作区（预期）、`remote` 本机无此盘（远端主机的路径，未核验、不计孤儿）、"
        "只有 `missing` 才进上面第 ⑤ 类。")
    add("- 行内每类计数（dup/zombie/blocked_stale/fail/orphan）只数 **P1·P2 需动作项**；P3 留痕只进 `P3=` 与报告正文。")
    add("- 巡检取数：`python tools/kanban_health.py --line` ⇒ 末行 `" + doc["line"] + "`")
    add("- 机读：`--json`（单行）或同名 `.json` 文件（schema `" + SCHEMA + "`）。")
    add("- 退出码：0 全绿 / 2 有 P2·P3 / 3 有 P1 / 1 内部错误 / 4 无板可扫。")
    add(f"- 生成器：`tools/kanban_health.py`（本报告是它唯一产物；改阈值 = 改该文件顶部 THR）｜生成于 {fmt_ts(now)}")
    add("")
    return "\n".join(L)


# ---------------------------------------------------------------- 自检（合成 DB，不碰真板）

_SCHEMA_SQL = """
CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT, status TEXT NOT NULL,
  priority INTEGER DEFAULT 0, created_by TEXT, created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,
  workspace_kind TEXT NOT NULL DEFAULT 'scratch', workspace_path TEXT, result TEXT, idempotency_key TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0, worker_pid INTEGER, worker_started_at INTEGER,
  last_failure_error TEXT, max_runtime_seconds INTEGER, last_heartbeat_at INTEGER, current_run_id INTEGER,
  claim_expires INTEGER, max_retries INTEGER, block_kind TEXT, block_recurrences INTEGER NOT NULL DEFAULT 0,
  completion_contract TEXT);
CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, profile TEXT, status TEXT NOT NULL,
  outcome TEXT, last_heartbeat_at INTEGER, started_at INTEGER NOT NULL, ended_at INTEGER, summary TEXT,
  metadata TEXT, error TEXT);
CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, run_id INTEGER, kind TEXT NOT NULL,
  payload TEXT, created_at INTEGER NOT NULL);
CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, author TEXT NOT NULL,
  body TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE task_attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, filename TEXT NOT NULL,
  stored_path TEXT NOT NULL, size INTEGER DEFAULT 0, created_at INTEGER NOT NULL);
CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id));
"""


def _mk_db(path: pathlib.Path, tasks: list[dict], runs: list[dict] | None = None,
           events: list[dict] | None = None, comments: list[dict] | None = None,
           attachments: list[dict] | None = None, links: list[dict] | None = None) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(_SCHEMA_SQL)
    for t in tasks:
        cols = ",".join(t.keys())
        ph = ",".join("?" * len(t))
        con.execute(f"insert into tasks ({cols}) values ({ph})", list(t.values()))
    for r in runs or []:
        cols = ",".join(r.keys())
        ph = ",".join("?" * len(r))
        con.execute(f"insert into task_runs ({cols}) values ({ph})", list(r.values()))
    for e in events or []:
        cols = ",".join(e.keys())
        ph = ",".join("?" * len(e))
        con.execute(f"insert into task_events ({cols}) values ({ph})", list(e.values()))
    for c in comments or []:
        con.execute("insert into task_comments (task_id,author,body,created_at) values (?,?,?,?)",
                    (c["task_id"], c.get("author", "t"), c["body"], c["created_at"]))
    for a in attachments or []:
        con.execute("insert into task_attachments (task_id,filename,stored_path,created_at) values (?,?,?,?)",
                    (a["task_id"], a["filename"], a["stored_path"], a.get("created_at", 0)))
    for l in links or []:
        con.execute("insert into task_links (parent_id,child_id) values (?,?)", (l["parent_id"], l["child_id"]))
    con.commit()
    con.close()


def _T(tid, title, status="todo", **kw):
    d = {"id": tid, "title": title, "status": status, "created_at": kw.pop("created_at", 1_000_000),
         "priority": 2, "consecutive_failures": 0, "block_recurrences": 0}
    d.update(kw)
    return d


def selftest() -> int:
    import tempfile

    bad = []
    n_checks = 0
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kbhealth_"))
    now = now_ts()

    def check(label, got, want):
        nonlocal n_checks
        n_checks += 1
        ok = got == want
        if not ok:
            bad.append(label)
        print("%s %-46s got=%r want=%r" % ("PASS" if ok else "FAIL", label, got, want))

    # ---- 合成板 A
    tasks = [
        # 重复：还在跑的一对
        _T("t_dup1", "能力②：MCP 工具面扩展建议（新增工具清单 + 草案）", "running", created_at=100),
        _T("t_dup2", "能力②：MCP 工具面扩展建议（新增工具清单 + 草案）", "todo", created_at=200),
        # 重复：都已 done
        _T("t_dup3", "数据①：近 30 天跑批统计（成功率/耗时/账号维度）", "done", created_at=300, result="已交付"),
        _T("t_dup4", "数据①：近30天跑批统计（成功率/耗时/账号维度）", "done", created_at=400),
        # 近似但不同（不该报）
        _T("t_neardiff", "09-27 缺口并行补齐【B 卡】：日常定时 2 / 7 / 12", "done", created_at=500),
        _T("t_neardiff2", "09-27 缺口并行补齐【C 卡】：日常定时 1 / 9 / 10", "done", created_at=600),
        # 僵尸：超 4h + 心跳断 90min
        _T("t_zomb", "僵尸卡", "running", started_at=now - 5 * 3600, last_heartbeat_at=now - 90 * 60,
           current_run_id=10, worker_pid=999999, claim_expires=now + 600, created_at=700),
        # 僵尸：从未心跳
        _T("t_zomb2", "spawn 后即挂", "running", started_at=now - 5 * 3600, last_heartbeat_at=None,
           current_run_id=11, created_at=800),
        # claim 过期未回收
        _T("t_claim", "claim 过期", "running", started_at=now - 600, last_heartbeat_at=now - 30,
           current_run_id=12, claim_expires=now - 40 * 60, created_at=900),
        # 长跑（心跳新鲜，墙钟 2h）
        _T("t_long", "长跑卡", "running", started_at=now - 2 * 3600, last_heartbeat_at=now - 60,
           current_run_id=13, created_at=1000),
        # blocked 超 6h 无注释（legacy 无 kind）
        _T("t_blk", "长期 blocked", "blocked", started_at=now - 9 * 3600, created_at=1100),
        # blocked：dependency 且父卡已 done
        _T("t_blkdep", "依赖已满足的僵持", "blocked", started_at=now - 2 * 3600, created_at=1200, block_kind="dependency"),
        _T("t_parent", "父卡", "done", created_at=90),
        # blocked 但 1h 前刚 block（不该报）
        _T("t_blkfresh", "刚 block", "blocked", started_at=now - 3600, created_at=1300, block_kind="needs_input"),
        # gave_up 未解
        _T("t_gaveup", "止损未解", "blocked", started_at=now - 3 * 3600, created_at=1400,
           last_failure_error="pid 1 exited with code 1", consecutive_failures=2),
        # cf 达到阈值仍在活跃车道
        _T("t_cf", "连败到位", "ready", created_at=1500, consecutive_failures=2, last_failure_error="boom"),
        # cf=1 热
        _T("t_hot", "一败", "running", created_at=1600, consecutive_failures=1, last_failure_error="boom"),
        # 迭代预算超限
        _T("t_budget", "预算用满", "running", created_at=1700, started_at=now - 3600, last_heartbeat_at=now - 30),
        # 反复崩溃
        _T("t_crash", "反复崩溃", "running", created_at=1800, consecutive_failures=1, last_failure_error="exited with code 1"),
        # 孤儿产物：声明了不存在的产出（declared）
        _T("t_orph", "产物丢失", "done", created_at=1900, completed_at=now - 3600, result="完成"),
        # done 但零证据（no_artifact）
        _T("t_noart", "空手收工", "done", created_at=1950, completed_at=now - 1800),
        # 批量崩溃（3 卡同窗口同家族）
        _T("t_bat1", "批量崩溃1", "running", created_at=2000, consecutive_failures=1, last_failure_error="pid 9 exited with code 1"),
        _T("t_bat2", "批量崩溃2", "running", created_at=2010, consecutive_failures=1, last_failure_error="pid 9 exited with code 1"),
        _T("t_bat3", "批量崩溃3", "running", created_at=2020, consecutive_failures=1, last_failure_error="pid 9 exited with code 1"),
        # done + 只有 summary（有证据 ⇒ 不报 no_artifact）
        _T("t_sums", "有摘要", "done", created_at=2030, completed_at=now - 900),
        # 已归档重复
        _T("t_arch", "能力②：MCP 工具面扩展建议（新增工具清单 + 草案）", "archived", created_at=80),
    ]
    runs = [
        {"task_id": "t_zomb", "status": "running", "started_at": now - 5 * 3600, "id": 10},
        {"task_id": "t_zomb2", "status": "running", "started_at": now - 5 * 3600, "id": 11},
        {"task_id": "t_claim", "status": "running", "started_at": now - 600, "id": 12},
        {"task_id": "t_long", "status": "running", "started_at": now - 2 * 3600, "id": 13},
        {"task_id": "t_crash", "status": "crashed", "outcome": "crashed", "started_at": now - 7200,
         "ended_at": now - 7100, "error": "pid 7 exited with code 1"},
        {"task_id": "t_crash", "status": "crashed", "outcome": "crashed", "started_at": now - 3600,
         "ended_at": now - 3500, "error": "pid 8 exited with code 1"},
        {"task_id": "t_orph", "status": "done", "outcome": "completed", "started_at": now - 7200,
         "ended_at": now - 3600, "summary": "产出 docs/__kbhealth_absent__.md",
         "metadata": json.dumps({"artifacts": [r"D:\__kbhealth_missing__\out.md"]})},
        {"task_id": "t_bat1", "status": "crashed", "outcome": "crashed", "started_at": now - 3 * 3600,
         "ended_at": now - 3 * 3600 + 60, "error": "pid 9 exited with code 1"},
        {"task_id": "t_bat2", "status": "crashed", "outcome": "crashed", "started_at": now - 3 * 3600 + 10,
         "ended_at": now - 3 * 3600 + 70, "error": "pid 9 exited with code 1"},
        {"task_id": "t_bat3", "status": "crashed", "outcome": "crashed", "started_at": now - 3 * 3600 + 20,
         "ended_at": now - 3 * 3600 + 80, "error": "pid 9 exited with code 1"},
        {"task_id": "t_sums", "status": "done", "outcome": "completed", "started_at": now - 1800,
         "ended_at": now - 900, "summary": "只写了一段结论，没声明产物"},
    ]
    events = [
        {"task_id": "t_blk", "kind": "blocked", "payload": json.dumps({"kind": "capability"}), "created_at": now - 9 * 3600, "run_id": None},
        {"task_id": "t_blkdep", "kind": "blocked", "payload": "{}", "created_at": now - 2 * 3600, "run_id": None},
        {"task_id": "t_blkfresh", "kind": "blocked", "payload": "{}", "created_at": now - 3600, "run_id": None},
        {"task_id": "t_gaveup", "kind": "gave_up", "payload": json.dumps({"failures": 2, "effective_limit": 2, "error": "pid 1 exited"}), "created_at": now - 2 * 3600, "run_id": None},
        {"task_id": "t_budget", "kind": "timed_out", "payload": json.dumps({"error": "Iteration budget exhausted (150/150)"}), "created_at": now - 1800, "run_id": None},
    ]
    links = [{"parent_id": "t_parent", "child_id": "t_blkdep"}]
    _mk_db(tmp / "kanban.db", tasks, runs, events, [], [], links)

    data = load_board("synth", tmp / "kanban.db")
    idx = index_board(data)
    thr = dict(THR)
    dups = detect_dups(data, idx, thr, now)
    zomb = detect_zombies(data, idx, thr, now)
    blk = detect_blocked(data, idx, thr, now)
    batches, batched = crash_batches(data, idx, thr, now)
    fail = detect_failures(data, idx, thr, now, batched)
    orph, ostats = detect_orphans(data, idx, now, (tmp,))

    def ids(fs, code=None):
        return sorted(f["ids"][0] for f in fs if code is None or f["code"] == code)

    check("dup: 命中活跃对+done 对", sorted(tuple(f["ids"]) for f in dups),
          [("t_dup1", "t_dup2"), ("t_dup3", "t_dup4")])
    check("dup: 近似不同不报", "t_neardiff" in ids(dups) or "t_neardiff2" in ids(dups), False)
    check("dup: 归档重复不计入", "t_arch" in [i for f in dups for i in f["ids"]], False)
    check("dup: 活跃对 P1", [f["sev"] for f in dups if f["ids"] == ["t_dup1", "t_dup2"]], [P1])
    check("resolved_dups 登记归档重复", [(x[0], x[1]) for x in resolved_dups(data, thr)],
          [("t_arch", "t_dup1")])
    check("zombie: 心跳断", ids(zomb, "zombie"), ["t_zomb"])
    check("zombie: 从未心跳", ids(zomb, "zombie_no_hb"), ["t_zomb2"])
    check("zombie: claim 过期", ids(zomb, "claim_stale"), ["t_claim"])
    check("zombie: 长跑单列", ids(zomb, "long_run"), ["t_budget", "t_long"])
    check("zombie: 健康卡不报", [f for f in zomb if f["cat"] == "zombie" and "t_dup1" in f["ids"]], [])
    check("blocked: 超时无注释", [f["code"] for f in blk if f["ids"] == ["t_blk"]], ["blocked_legacy_kind"])
    check("blocked: 依赖僵持 P1", [f["sev"] for f in blk if f["ids"] == ["t_blkdep"]], [P1])
    check("blocked: 新 block 不报", [f for f in blk if f["ids"] == ["t_blkfresh"]], [])
    check("fail: gave_up", ids(fail, "gave_up"), ["t_gaveup"])
    check("fail: 止损到位", ids(fail, "limit_reached"), ["t_cf"])
    check("fail: 一败热", ids(fail, "limit_hot"), ["t_bat1", "t_bat2", "t_bat3", "t_crash", "t_hot"])
    check("fail: 批量崩溃聚合 1 条", [tuple(f["ids"]) for f in batches],
          [("t_bat1", "t_bat2", "t_bat3")])
    check("fail: 批量成员降 P3 留痕",
          [f["sev"] for f in fail if f["code"] == "limit_hot" and f["ids"][0].startswith("t_bat")], [P3, P3, P3])
    check("fail: 预算超限", ids(fail, "timed_out_budget"), ["t_budget"])
    check("fail: 反复崩溃", ids(fail, "crash_repeat"), ["t_crash"])
    check("orphan: 声明缺失", ids(orph, "artifact_missing"), ["t_orph"])
    check("orphan: 摘要弱证据 P3", ids(orph, "prose_path_missing"), ["t_orph"])
    check("orphan: 有产出的 done 不报 no_artifact",
          [f for f in orph if f["code"] == "no_artifact" and f["ids"] == ["t_orph"]], [])
    check("orphan: 零证据 done 报 no_artifact", ids(orph, "no_artifact"),
          ["t_dup4", "t_neardiff", "t_neardiff2", "t_noart", "t_parent"])
    check("orphan: 只有 summary 的 done 不报", [f for f in orph if f["ids"] == ["t_sums"]], [])
    check("orphan: 健康 done 不报", [f for f in orph if f["ids"] == ["t_dup3"]], [])
    # 路径 token 过滤：glob/占位/省略号不取；标点要断在「（」前
    got_tokens = sorted(p for _s, p in collect_paths(
        {"artifacts": ["D:/x/{topology,arity}.json", "docs/OK.md（改：§5",
                       r"C:\Users\xiach\...\refs\field-tables.md", "E:/AUTO-MAS/app/x.py"]}, ""))
    check("path 过滤: glob/省略号剔除、标点截断", got_tokens,
          sorted(["docs/OK.md", "E:/AUTO-MAS/app/x.py"]))
    _miss_drive = next(d for d in "QYZ" if not os.path.exists(d + ":\\"))
    st, _ = path_state(_miss_drive + ":\\nope\\x.md", None, {}, tmp / "workspaces", (tmp,))
    check("path_state: 本机无此盘 = remote", st, "remote")
    check("expand_variants: foo.json/.md", expand_variants("runtime/reports/a.json/.md"),
          ["runtime/reports/a.json", "runtime/reports/a.md"])
    (tmp / "docs").mkdir(exist_ok=True)
    (tmp / "docs" / "OK.md").write_text("x", encoding="utf-8")
    st, _ = path_state("docs/OK.md/.txt", None, {}, tmp / "workspaces", (tmp,))
    check("path_state: 变体存在 = exists", st, "exists")
    check("orphan_stats: 计数可复算", ostats["checked"] >= ostats.get("exists", 0), True)
    # 路径分类：scratch 工作区缺失 = cleaned（预期）
    st, _ = path_state(str(tmp / "workspaces" / "t_x" / "out.md"), None, {}, tmp / "workspaces")
    check("path_state: scratch 清理 = cleaned", st, "cleaned")
    st, _ = path_state("C:/definitely/not/here/x.md", None, {}, tmp / "workspaces")
    check("path_state: 真缺 = missing", st, "missing")
    st, _ = path_state(DATA_EXISTS_SENTINEL := str(pathlib.Path(__file__).resolve()), None, {}, tmp / "workspaces")
    check("path_state: 存在 = exists", st, "exists")
    st, _ = path_state("out.md", None, {"out.md": "X:/copy/out.md"}, tmp / "workspaces")
    check("path_state: 同名附件 = staged", st, "staged")

    # 汇总 / 行 / 退出码
    bres = [scan_board("synth", tmp / "kanban.db", thr, now)]
    doc = build_doc(bres, thr, now, "2026-01-01 00:00")
    check("doc: status=CRIT", doc["status"], "CRIT")
    check("doc: P1>0", doc["totals"][P1] > 0, True)
    check("line: 前缀与 status", doc["line"].startswith("KANBAN_HEALTH=ts=2026-01-01 00:00,boards=1,"
          ) and doc["line"].endswith("status=CRIT"), True)
    check("line: 可被 kv 解析", dict(x.split("=", 1) for x in doc["line"].split("=", 1)[1].split(","))["status"], "CRIT")
    check("md: 含建议动作与命令", ("建议：" in render_md(doc, thr, now, 40)) and ("hermes kanban" in render_md(doc, thr, now, 40)), True)
    check("md: 含已归档重复附录", "已归档的重复" in render_md(doc, thr, now, 40), True)
    # 空板 → OK
    empty = tmp / "empty"
    empty.mkdir()
    _mk_db(empty / "kanban.db", [_T("t_ok", "干干净净", "done", created_at=1, result="ok")])
    b2 = scan_board("empty", empty / "kanban.db", thr, now)
    check("空板无 finding", b2["findings"], [])
    doc2 = build_doc([b2], thr, now, "2026-01-01 00:00")
    check("空板 status=OK", doc2["status"], "OK")
    check("空板 line status=OK", doc2["line"].endswith("status=OK"), True)
    # 阈值覆盖生效
    thr2 = dict(thr)
    thr2["zombie_h"] = 100.0
    z2 = detect_zombies(data, idx, thr2, now)
    check("阈值覆盖: zombie_h=100 抑制僵尸", [f for f in z2 if f["code"] in ("zombie", "zombie_no_hb")], [])

    print("-" * 60)
    print("SELFTEST=%s (%d checks, %d failed)" % ("PASS" if not bad else "FAIL", n_checks, len(bad)))
    if bad:
        print("failed:", ", ".join(bad))
    return 0 if not bad else 1


# ---------------------------------------------------------------- main

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="看板治理体检（只读）：重复卡/僵尸卡/长期 blocked/崩溃超限/孤儿产物")
    ap.add_argument("--board", action="append", help="只扫指定板 slug（可多次）")
    ap.add_argument("--boards-root", help="看板根目录（默认 HERMES_KANBAN_DB 推导，再退 C:\\...\\kanban\\boards）")
    ap.add_argument("--json", action="store_true", help="单行 JSON（含 line），不写盘")
    ap.add_argument("--line", action="store_true", help="只打一行 KANBAN_HEALTH=")
    ap.add_argument("--no-write", action="store_true", help="不写报告文件")
    ap.add_argument("--selftest", action="store_true", help="本地自检（合成 DB）")
    ap.add_argument("--limit", type=int, default=40, help="md 每类最多列几条（默认 40）")
    ap.add_argument("--ts", help="报告时间戳串（默认当前，格式 %%Y%%m%%d_%%H%%M%%S）")
    ap.add_argument("--zombie-h", type=float, default=THR["zombie_h"])
    ap.add_argument("--hb-gap-min", type=float, default=THR["hb_gap_min"])
    ap.add_argument("--blocked-h", type=float, default=THR["blocked_h"])
    ap.add_argument("--dup-ratio", type=float, default=THR["dup_ratio"])
    ap.add_argument("--long-run-min", type=float, default=THR["long_run_min"])
    ap.add_argument("--board-cap", type=int, default=THR["board_cap"])
    ap.add_argument("--fail-limit", type=int, default=THR["fail_limit"])
    ap.add_argument("--batch-window-min", type=float, default=THR["batch_window_min"],
                    help="批量崩溃聚合窗口（分钟，默认 20）")
    ap.add_argument("--extra-root", action="append", help="相对产物路径的额外解析根（可多次；默认自动带 ROOT 与相邻工程）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    thr = {"zombie_h": a.zombie_h, "hb_gap_min": a.hb_gap_min, "blocked_h": a.blocked_h,
           "dup_ratio": a.dup_ratio, "long_run_min": a.long_run_min, "board_cap": a.board_cap,
           "fail_limit": a.fail_limit, "claim_late_min": THR["claim_late_min"],
           "batch_window_min": THR["batch_window_min"]}

    # 相对路径的解析根：本仓库 + 相邻工程（模块 3 的卡常引用 arknights-community / MAS-KB 的路径）+ --extra-root
    bases: list[pathlib.Path] = [ROOT]
    bases += [ROOT.parent / n for n in DEFAULT_EXTRA_ROOTS if (ROOT.parent / n).is_dir()]
    bases += [pathlib.Path(x) for x in (a.extra_root or [])]
    bases_t = tuple(dict.fromkeys(bases))

    if a.boards_root:
        root = pathlib.Path(a.boards_root)
    else:
        env_db = os.environ.get("HERMES_KANBAN_DB")
        root = pathlib.Path(env_db).parent.parent if env_db else \
            pathlib.Path.home() / "AppData" / "Local" / "hermes" / "kanban" / "boards"
    boards = find_boards(root, a.board)
    if not boards:
        print(f"KANBAN_HEALTH=ts=ERROR,no-board-found,root={root}", file=sys.stderr)
        return 4

    now = now_ts()
    scans = []
    for slug, db in boards:
        try:
            bases = list(bases_t)
            meta = {}
            bj = db.parent / "board.json"
            if bj.is_file():
                try:
                    meta = json.loads(bj.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    meta = {}
            wd = meta.get("default_workdir")
            if wd and pathlib.Path(wd).is_dir() and pathlib.Path(wd) not in bases:
                bases.append(pathlib.Path(wd))
            scans.append(scan_board(slug, db, thr, now, tuple(bases)))
        except Exception as exc:  # noqa: BLE001
            print(f"warn: 板 {slug} 扫描失败：{exc}", file=sys.stderr)
    if not scans:
        return 4

    ts_str = dt.datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S") if not a.ts else a.ts
    human_ts = f"{ts_str[:4]}-{ts_str[4:6]}-{ts_str[6:8]} {ts_str[9:11]}:{ts_str[11:13]}" if "_" in ts_str else ts_str
    doc = build_doc(scans, thr, now, human_ts, ts_str)

    if a.line:
        print(doc["line"])
        return 0
    if a.json:
        print(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
        return 0

    md_path = json_path = None
    if not a.no_write:
        REPORTS.mkdir(parents=True, exist_ok=True)
        md_path = REPORTS / f"kanban_health_{ts_str}.md"
        json_path = REPORTS / f"kanban_health_{ts_str}.json"
        md_path.write_text(render_md(doc, thr, now, a.limit), encoding="utf-8")
        json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    # 终端摘要
    print(f"看板治理体检 {human_ts} · " + " ".join(f"{b['slug']}:{b['cards']}卡/{len(b['findings'])}项" for b in doc["boards"]))
    for cat, title in CAT_TITLES.items():
        items = [f for b in doc["boards"] for f in b["findings"] if f["cat"] == cat]
        if not items:
            continue
        print(f"  {title}：{len(items)}")
        for f in items[:8]:
            print(f"    [{f['sev']}] {f['title']}")
            print(f"          → {f['action'][:96]}")
        if len(items) > 8:
            print(f"    … 其余 {len(items) - 8} 条见报告")
    if md_path:
        print(f"报告：{md_path}")
        print(f"机读：{json_path}")
    print(doc["line"])
    return 0 if doc["status"] == "OK" else (3 if doc["status"] == "CRIT" else 2)


if __name__ == "__main__":
    sys.exit(main())
