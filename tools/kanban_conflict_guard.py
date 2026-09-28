#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""文件级互斥闸（只读）——建卡 / 派发前检测「共享文件冲突」，给串行建议。

背景：2026-09-28 实测同一批 running 卡里存在 2 对「同文件并发写」：
  * `t_403cd83b`（UPCOMING 接线）与 `t_4cd871de`（kanban_health 接线）都要改 `tools/mas_observe_mcp.py`；
  * `t_a190ca4b`（审计接线）与 `t_1df99ae5`（实例租约接入）都改 `tools/mas_run_profile.py`（工作树里
    同时含 `mas_audit.safe_append` 与 `lease_acquire/lease_release` 两族改动）。
宿主并发阀门（max_in_progress 等）均为「不限」，文件级冲突只能靠纪律 + 本闸兜。

本工具做三件事（**只读看板，永不 claim/comment/edit/complete，也从不写 kanban.db**）：
  ① 从卡 body 提取「卡 → 文件清单」（正则锚定目录 tools|docs|services|ui|network|manager|infra|config，
     扩展名 py|md|json|yaml|yml|ts|js|html|css|sh|ps1；去重 + 分隔符归一 + 长路径/反斜杠归一到仓库相对形态）；
  ② 对在队卡（默认 running + ready + todo）全量两两求「共享文件」⇒ 冲突对；
  ③ 给出串行建议：running 冲突 ⇒ 错峰/串行并点名**当前持有者卡号**；都未跑 ⇒ 建议 `hermes kanban link`
     （父=先跑者，按 priority DESC, created_at ASC 与派发顺序同序）。

用法（`$PY312` = C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe）：
  python tools/kanban_conflict_guard.py                       # 默认 --line：一行 KANBAN_CONFLICT=（末行）
  python tools/kanban_conflict_guard.py --verbose             # 人读：卡→文件清单 + 冲突 + 建议（末行仍为判据行）
  python tools/kanban_conflict_guard.py --json                # 单行 JSON（机读；含 cards 映射）
  python tools/kanban_conflict_guard.py --card t_xxxxxxx      # 候选=某张卡（任意状态）：只报与在队卡的冲突
  python tools/kanban_conflict_guard.py --files tools/a.py,docs/b.md
  python tools/kanban_conflict_guard.py --body-file draft.md  # 建卡前预检：候选=一份卡面草稿正文
  python tools/kanban_conflict_guard.py --out P.json          # 除该参数外**零写盘**
  python tools/kanban_conflict_guard.py --selftest            # 离线自检（合成板，不碰真板）
选项：--board SLUG --boards-root P --db P --scope running,ready,todo --verbose --json --line --out P

判据行（末行，机检用）：
  KANBAN_CONFLICT=OK conflicts=none
  KANBAN_CONFLICT=WARN:<n> conflicts=t_a:t_b(shared=f1+f2),t_c:t_d(shared=f3)
  KANBAN_CONFLICT=ERROR:<kind> detail=<...>
退出码：0 = OK（可派发）；2 = WARN（有冲突，禁同批派发）或 fail-closed 错误（未知卡 id /
半截输入 / 看板不可读 / 内部异常——不抛裸异常，统一转 ok:false）。**除 --out 外零写盘。**

语义边界（误报与漏报，都在 docs/KANBAN_CONFLICT_GUARD.md §局限）：
  * 提取粒度 = 「卡面提到该文件」≠「一定会写它」——本闸宁可多报（放宽 = 保守），WARN 后由人/lead 断；
    反之卡面完全不提文件时本闸**看不见**其写行为（漏报面）。
  * 只看卡 body；不看 title / comments / result / 磁盘 mtime（要看刚刚完成卡留下的工作树改动，
    用 `--card <刚完成的卡>` 把它的清单当候选再比一次）。
  * 单板范围（默认 = HERMES_KANBAN_BOARD/HERMES_KANBAN_DB 推导的当前板）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "maaorch.kanban_conflict_guard/1"

DEFAULT_SCOPE = ("running", "ready", "todo")  # 「在队/在飞」= 会真实写文件的三类
SCOPE_ALLOWED = ("running", "ready", "todo", "ready", "review", "blocked", "done")  # review/blocked/done 可选
ANCHOR_DIRS = ("tools", "docs", "services", "ui", "network", "manager", "infra", "config")
EXT_ALT = "py|md|json|yaml|yml|ts|js|html|css|sh|ps1"
# body 归一化（\ → /）后，抓「连续路径字符且以允许扩展名结尾」的 token
TOKEN_RE = re.compile(r"[A-Za-z0-9_./\-]*\.(?:%s)\b" % EXT_ALT, re.I)
CANDIDATE_ID = "(candidate)"  # --files / --body-file 候选的占位卡号


class GuardError(Exception):
    """带机读 kind 的失败（fail-closed）：main 统一转 ok:false + rc=2，不抛裸异常。"""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def now_ts() -> int:
    return int(time.time())


def fmt_ts(epoch) -> str:
    if not epoch:
        return "-"
    return dt.datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 文件清单提取

def canon_token(tok: str) -> str | None:
    """把 token 归一到「仓库相对形态」；无锚定目录则返回 None。

    处理：反斜杠→正斜杠、去列表前缀 `-`、`./` 前缀、空段；从**第一个**锚定目录段起截尾。
    例：`D:\\1-2\\MAAOrch\\tools\\x.py` → `tools/x.py`；`//host/tools/x.py`（URL 尾）→ None。
    """
    t = str(tok or "").strip().strip("\"'`")
    t = t.replace("\\", "/")
    t = t.lstrip("-")               # 列表项 " - tools/x.py"
    while t.startswith("./"):
        t = t[2:]
    t = t.rstrip("/")
    if not t or t.startswith("//"):  # URL 尾（http://host/tools/x.py 会被切成 //host/tools/x.py）
        return None
    parts = [p for p in t.split("/") if p not in ("", ".")]
    if len(parts) < 2:
        return None
    for i, seg in enumerate(parts[:-1]):
        if seg.lower() in ANCHOR_DIRS:
            return "/".join(parts[i:])
    return None


def fkey(display: str) -> str:
    """比较键：大小写不敏感（Windows 文件系统语义）；显示仍用首个出现的原样。"""
    return display.lower()


def extract_files(text: str | None) -> list[str]:
    """从正文提取文件清单（去重保序，显示用首次出现的归一形态）。"""
    if not text:
        return []
    norm = str(text).replace("\\", "/")
    seen: dict[str, str] = {}
    for m in TOKEN_RE.finditer(norm):
        c = canon_token(m.group(0))
        if not c:
            continue
        k = fkey(c)
        if k not in seen:
            seen[k] = c
    return list(seen.values())


def parse_explicit_files(spec: str | None) -> list[str]:
    """解析 --files a.py,b.md：逗号分隔；允许全路径（会归一到锚定尾）；空输入 fail-closed。"""
    items = [x.strip() for x in str(spec or "").split(",")]
    items = [x for x in items if x]
    if not items:
        raise GuardError("empty_input",
                         "--files 为空：请给逗号分隔的仓库相对路径（如 tools/mas_run_profile.py,docs/x.md）")
    out = []
    for it in items:
        c = canon_token(it) or it.replace("\\", "/").strip().lstrip("./")
        if not c:
            raise GuardError("empty_input", f"--files 含空项：{spec!r}")
        out.append(c)
    return out


# ---------------------------------------------------------------- 看板读取（只读）

def _derive_boards_root(a) -> pathlib.Path:
    if a.boards_root:
        return pathlib.Path(a.boards_root)
    env_db = os.environ.get("HERMES_KANBAN_DB")
    if env_db:
        return pathlib.Path(env_db).parent.parent
    return pathlib.Path.home() / "AppData" / "Local" / "hermes" / "kanban" / "boards"


def resolve_db(a) -> tuple[str, pathlib.Path]:
    """解析 (board_slug, kanban.db)：--db > --board > HERMES_KANBAN_DB > HERMES_KANBAN_BOARD > 唯一板。"""
    if a.db:
        db = pathlib.Path(a.db)
        if not db.is_file():
            raise GuardError("board_unreadable", f"--db 不存在：{db}")
        return db.parent.name, db
    root = _derive_boards_root(a)
    env_db = os.environ.get("HERMES_KANBAN_DB")
    if not a.board and env_db and pathlib.Path(env_db).is_file():
        return pathlib.Path(env_db).parent.name, pathlib.Path(env_db)
    slug = a.board or os.environ.get("HERMES_KANBAN_BOARD")
    if slug:
        db = root / slug / "kanban.db"
        if not db.is_file():
            raise GuardError("board_unreadable", f"板上找不到 kanban.db：{db}")
        return slug, db
    cands = [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith("_")
             and (d / "kanban.db").is_file()] if root.is_dir() else []
    if len(cands) == 1:
        return cands[0].name, cands[0] / "kanban.db"
    raise GuardError("board_unreadable",
                     f"无法确定看板（--board/--db 二选一，或设 HERMES_KANBAN_BOARD）：root={root} 候选={[d.name for d in cands]}")


def _table_cols(con, table: str) -> set[str]:
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def open_ro(db: pathlib.Path) -> sqlite3.Connection:
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise GuardError("board_unreadable", f"只读打开失败：{db}：{exc}") from None
    con.row_factory = sqlite3.Row
    return con


def read_cards(db: pathlib.Path, slug: str, statuses, only_id: str | None = None) -> list[dict]:
    """读卡（只读）。返回 [{id,status,priority,created_at,title,files}]；only_id 时忽略 statuses。"""
    con = open_ro(db)
    try:
        have = _table_cols(con, "tasks")
        if not have:
            raise GuardError("board_unreadable", f"tasks 表不存在或不可读：{db}")
        use = [c for c in ("id", "status", "priority", "created_at", "title", "body") if c in have]
        if "id" not in use or "body" not in use:
            raise GuardError("board_unreadable", f"tasks 表缺列（需要 id/body）：有 {sorted(have)}")
        cols = ", ".join(use)
        try:
            if only_id:
                rows = con.execute(f"select {cols} from tasks where id=?", (only_id,)).fetchall()
            else:
                ph = ", ".join("?" * len(statuses))
                rows = con.execute(f"select {cols} from tasks where status in ({ph}) order by id",
                                   tuple(statuses)).fetchall()
        except sqlite3.Error as exc:
            raise GuardError("board_unreadable", f"查询失败：{exc}") from None
    finally:
        con.close()

    cards = []
    for r in rows:
        d = dict(r)
        cards.append({
            "id": d.get("id"), "status": d.get("status") or "?",
            "priority": d.get("priority") if d.get("priority") is not None else 0,
            "created_at": d.get("created_at") or 0,
            "title": (d.get("title") or "")[:60],
            "files": extract_files(d.get("body")),
        })
    return cards


# ---------------------------------------------------------------- 冲突判定

def shared_files(a: dict, b: dict) -> list[str]:
    ka = {fkey(f) for f in a["files"]}
    out = []
    for f in b["files"]:
        if fkey(f) in ka:
            out.append(f)
    return sorted(out, key=fkey)


def suggest_pair(a: dict, b: dict, shared: list[str]) -> str:
    """串行建议：running 有持有者 ⇒ 错峰/串行点名；都未跑 ⇒ link（父=先跑者）。"""
    shown = "、".join(shared[:3]) + ("…" if len(shared) > 3 else "")
    running = [c for c in (a, b) if c["status"] == "running"]
    if running:
        holders = "、".join(f"{c['id']}(running)" for c in running)
        others = "、".join(c["id"] for c in (a, b) if c["status"] != "running") or "本卡"
        return (f"串行/错峰：{shown} 的当前持有者 = {holders}；等其收口再派 {others}，"
                f"或先把 {others} 的范围改到无冲突文件（真并发写 = 后写覆盖前写）")
    first, second = sorted([a, b], key=lambda c: (-int(c.get("priority") or 0),
                                                  int(c.get("created_at") or 0), c["id"]))
    return (f"同批待派：建议先 {first['id']} 后 {second['id']}（按派发序 priority DESC, created_at ASC）"
            f"；挂依赖：hermes kanban link {first['id']} {second['id']}")


def find_conflicts(cards: list[dict], involving: str | None = None) -> list[dict]:
    """两两求共享文件；involving=只看含该卡号的冲突（候选模式）。"""
    pairs = []
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            a, b = cards[i], cards[j]
            if involving and involving not in (a["id"], b["id"]):
                continue
            shared = shared_files(a, b)
            if not shared:
                continue
            x, y = sorted([a, b], key=lambda c: c["id"])
            pairs.append({"cards": [x["id"], y["id"]], "shared": shared,
                          "suggest": suggest_pair(x, y, shared)})
    pairs.sort(key=lambda p: (p["cards"][0], p["cards"][1]))
    return pairs


def build_line(status: str, pairs: list[dict]) -> str:
    if status == "OK":
        return "KANBAN_CONFLICT=OK conflicts=none"
    chunks = []
    for p in pairs:
        a, b = p["cards"]
        chunks.append(f"{a}:{b}(shared={'+'.join(p['shared'])})")
    return f"KANBAN_CONFLICT=WARN:{len(pairs)} conflicts={','.join(chunks)}"


# ---------------------------------------------------------------- 主流程（main 与 selftest 共用）

def build_doc(db: pathlib.Path, slug: str, statuses, cand_kind: str | None = None,
              cand_value=None) -> dict:
    """核心路径：读在队卡（+可选候选）→ 冲突 → doc。任何失败抛 GuardError。"""
    statuses = tuple(statuses or DEFAULT_SCOPE)
    bad = [s for s in statuses if s not in SCOPE_ALLOWED]
    if bad:
        raise GuardError("bad_input", f"--scope 含未知状态 {bad}（允许：{'/'.join(sorted(set(SCOPE_ALLOWED)))}）")
    cards = read_cards(db, slug, statuses)

    cand = None
    if cand_kind == "card":
        got = read_cards(db, slug, statuses, only_id=str(cand_value))
        if not got:
            raise GuardError("unknown_card", f"看板 {slug} 上没有卡 {cand_value}")
        cand = got[0]
        # 候选卡若本身在队 ⇒ 用同一副本、去重（自比永不算冲突）
        cards = [c for c in cards if c["id"] != cand["id"]]
    elif cand_kind == "files":
        files = parse_explicit_files(cand_value)
        cand = {"id": CANDIDATE_ID, "status": "candidate", "priority": 2, "created_at": now_ts(),
                "title": "(候选文件清单)", "files": files}
    elif cand_kind == "body":
        p = pathlib.Path(str(cand_value))
        if not p.is_file():
            raise GuardError("bad_input", f"--body-file 不存在：{p}")
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise GuardError("bad_input", f"--body-file 读取失败：{p}：{exc}") from None
        cand = {"id": CANDIDATE_ID, "status": "candidate", "priority": 2, "created_at": now_ts(),
                "title": f"(body-file: {p.name})", "files": extract_files(text)}
    elif cand_kind is not None:
        raise GuardError("bad_input", f"未知候选类型：{cand_kind}")

    pool = cards + ([cand] if cand else [])
    pairs = find_conflicts(pool, involving=(cand["id"] if cand else None))
    status = "WARN" if pairs else "OK"
    line = build_line(status, pairs)
    ts = dt.datetime.fromtimestamp(now_ts()).strftime("%Y-%m-%d %H:%M:%S")
    doc = {
        "schema": SCHEMA, "ts": ts, "board": slug, "db": str(db),
        "scope": list(statuses), "mode": cand_kind or "board",
        "ok": True, "status": status,
        "conflicts": pairs, "cards_scanned": len(cards),
        "cards": [{"id": c["id"], "status": c["status"], "priority": c["priority"], "files": c["files"]}
                  for c in (cards + ([cand] if cand else []))],
        "line": line,
    }
    return doc


def error_doc(kind: str, detail: str, board: str = "?", db: str = "?") -> dict:
    return {"schema": SCHEMA, "ts": dt.datetime.fromtimestamp(now_ts()).strftime("%Y-%m-%d %H:%M:%S"),
            "board": board, "db": db, "ok": False, "status": "ERROR",
            "error": {"kind": kind, "detail": detail}, "conflicts": [], "cards_scanned": 0}


def render_verbose(doc: dict) -> str:
    L = [f"文件级互斥闸 · board={doc['board']} · scope={'/'.join(doc.get('scope', []))} · {doc['ts']}",
         f"扫描卡数={doc['cards_scanned']}（在队） 候选模式={doc['mode']}"]
    L.append("")
    L.append("卡 → 文件清单：")
    for c in doc.get("cards", []):
        fl = ", ".join(c["files"]) if c["files"] else "(无文件引用)"
        L.append(f"  [{c['status']:<9}] {c['id']} p{c['priority']}  {fl}")
    L.append("")
    if doc.get("conflicts"):
        L.append(f"冲突对（{len(doc['conflicts'])}）：")
        for p in doc["conflicts"]:
            a, b = p["cards"]
            L.append(f"  {a} × {b} @ {' + '.join(p['shared'])}")
            L.append(f"      → {p['suggest']}")
    else:
        L.append("冲突对：无。")
    L.append("")
    L.append(doc["line"])
    return "\n".join(L)


# ---------------------------------------------------------------- 自检（合成板，不碰真板）

_SCHEMA_SQL = """
CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT, status TEXT NOT NULL,
  priority INTEGER DEFAULT 0, created_at INTEGER NOT NULL, completed_at INTEGER);
"""


def _mk_board(path: pathlib.Path, tasks: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript(_SCHEMA_SQL)
    con.executemany("insert into tasks (id,title,body,status,priority,created_at) values (?,?,?,?,?,?)", tasks)
    con.commit()
    con.close()


def selftest() -> int:
    import tempfile

    bad, n = [], 0

    def check(label, got, want):
        nonlocal n
        n += 1
        ok = got == want
        if not ok:
            bad.append(label)
        print("%s %-52s got=%r want=%r" % ("PASS" if ok else "FAIL", label, got, want))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kbconflict_"))
    db = tmp / "boards" / "synth" / "kanban.db"
    _mk_board(db, [
        # c1 running：长清单 + 重复提及 + 反斜杠混写
        ("t_c1", "甲", "改 tools/alpha.py 与 docs/note.md；又见 tools\\alpha.py（重复提及）",
         "running", 2, 100),
        # c2 ready：与 c1 共享 tools/alpha.py
        ("t_c2", "乙", "改 tools/alpha.py", "ready", 3, 200),
        # c3 running：Windows 长路径归一
        ("t_c3", "丙", r"改 D:\1-2\MAAOrch\tools\beta.py，顺带 docs/x.md", "running", 2, 300),
        # c4 todo：反斜杠 + 大小写不同 ⇒ 仍应与 c3 冲突
        ("t_c4", "丁", r"改 tools\BETA.py", "todo", 1, 400),
        # c5 running：空 body（无文件，不该报任何冲突）
        ("t_c5", "戊", "", "running", 2, 500),
        # c6 done：默认不在范围（即使与 c1 撞 tools/alpha.py）
        ("t_c6", "己", "改 tools/alpha.py", "done", 2, 600),
        # c7 ready + c8 todo：都未跑、互相共享 ⇒ suggest 应给 link
        ("t_c7", "庚", "改 docs/dual.md", "ready", 2, 700),
        ("t_c8", "辛", "改 docs/dual.md", "todo", 2, 800),
    ])

    cards = read_cards(db, "synth", DEFAULT_SCOPE)
    ids = [c["id"] for c in cards]
    check("scope: 默认只含 running/ready/todo（done 不入）", ids, ["t_c1", "t_c2", "t_c3", "t_c4", "t_c5", "t_c7", "t_c8"])
    by = {c["id"]: c for c in cards}
    check("提取: 去重 + 反斜杠归一", by["t_c1"]["files"], ["tools/alpha.py", "docs/note.md"])
    check("提取: Windows 长路径归一到仓库相对", by["t_c3"]["files"], ["tools/beta.py", "docs/x.md"])
    check("提取: 反斜杠 + 大小写", by["t_c4"]["files"], ["tools/BETA.py"])
    check("提取: 空 body 无文件", by["t_c5"]["files"], [])

    pairs = find_conflicts(cards)
    got_pairs = {(p["cards"][0], p["cards"][1], tuple(p["shared"])) for p in pairs}
    want_pairs = {("t_c1", "t_c2", ("tools/alpha.py",)),
                  ("t_c3", "t_c4", ("tools/BETA.py",)),
                  ("t_c7", "t_c8", ("docs/dual.md",))}
    check("冲突: 命中 3 对且 shared 正确（含长路径/反斜杠/大小写）", got_pairs, want_pairs)
    check("冲突: 不共享的卡对不报", any(p["cards"] == ["t_c1", "t_c3"] for p in pairs), False)
    check("冲突: 空文件卡不参与任何冲突", any("t_c5" in p["cards"] for p in pairs), False)

    # 同卡自比不算冲突：候选=在队卡 c1 ⇒ 只报 c1×c2
    d_c1 = build_doc(db, "synth", DEFAULT_SCOPE, "card", "t_c1")
    check("同卡自比不算冲突（候选=在队 c1 → 只剩 c1×c2）",
          [(p["cards"], p["shared"]) for p in d_c1["conflicts"]],
          [(["t_c1", "t_c2"], ["tools/alpha.py"])])
    sug_run = d_c1["conflicts"][0]["suggest"]
    check("suggest: running 冲突点名持有者", ("串行" in sug_run and "t_c1(running)" in sug_run), True)
    d_c7 = build_doc(db, "synth", DEFAULT_SCOPE, "card", "t_c7")
    sug_link = d_c7["conflicts"][0]["suggest"]
    check("suggest: 都未跑 → link（父=先跑者）", ("hermes kanban link t_c7 t_c8" in sug_link), True)

    d_all = build_doc(db, "synth", DEFAULT_SCOPE)
    check("line: WARN:3 且机器可读", d_all["line"].startswith("KANBAN_CONFLICT=WARN:3 conflicts=")
          and "t_c1:t_c2(shared=tools/alpha.py)" in d_all["line"], True)
    check("json: 结构稳定（ok/status/conflicts/cards_scanned/line）", 
          (d_all["ok"], d_all["status"], len(d_all["conflicts"]), d_all["cards_scanned"],
           isinstance(d_all["line"], str)), (True, "WARN", 3, 7, True))
    check("json: cards 映射含候选标记位", [c["id"] for c in d_all["cards"]][:2], ["t_c1", "t_c2"])

    # 候选模式：--files（含全路径归一）
    d_fl = build_doc(db, "synth", DEFAULT_SCOPE, "files", "D:/1-2/MAAOrch/tools/alpha.py, docs/dual.md")
    check("候选 --files: 全路径归一后命中 c1/c2/c7/c8",
          sorted(tuple(p["cards"]) for p in d_fl["conflicts"]),
          [("(candidate)", "t_c1"), ("(candidate)", "t_c2"), ("(candidate)", "t_c7"), ("(candidate)", "t_c8")])
    # 候选模式：--body-file
    bf = tmp / "draft.md"
    bf.write_text("改动 tools/beta.py；再动 docs/x.md", encoding="utf-8")
    d_bf = build_doc(db, "synth", DEFAULT_SCOPE, "body", bf)
    check("候选 --body-file: 命中 c3/c4", sorted(tuple(p["cards"]) for p in d_bf["conflicts"]),
          [("(candidate)", "t_c3"), ("(candidate)", "t_c4")])
    bf2 = tmp / "empty.md"
    bf2.write_text("", encoding="utf-8")
    d_ok = build_doc(db, "synth", DEFAULT_SCOPE, "body", bf2)
    check("候选 --body-file 空正文 ⇒ OK 无冲突", (d_ok["ok"], d_ok["status"], d_ok["conflicts"]), (True, "OK", []))

    # fail-closed 筷
    def kind_of(fn):
        try:
            fn()
            return "no-error"
        except GuardError as e:
            return e.kind

    check("fail-closed: 未知卡 id", kind_of(lambda: build_doc(db, "synth", DEFAULT_SCOPE, "card", "t_nope")),
          "unknown_card")
    check("fail-closed: --files 空输入", kind_of(lambda: build_doc(db, "synth", DEFAULT_SCOPE, "files", " , ")),
          "empty_input")
    check("fail-closed: --scope 未知状态", kind_of(lambda: build_doc(db, "synth", ("running", "bogus"))),
          "bad_input")
    check("fail-closed: 看板不可读（db 不存在）",
          kind_of(lambda: resolve_db(argparse.Namespace(db=str(tmp / "nope.db"), board=None, boards_root=None))),
          "board_unreadable")
    check("fail-closed: body-file 不存在",
          kind_of(lambda: build_doc(db, "synth", DEFAULT_SCOPE, "body", str(tmp / "nope.md"))), "bad_input")
    check("error_doc: ok=false + error{kind,detail}", (error_doc("x", "y")["ok"], error_doc("x", "y")["error"]["kind"]),
          (False, "x"))

    print("-" * 64)
    print("SELFTEST=%s (%d checks, %d failed)" % ("PASS" if not bad else "FAIL", n, len(bad)))
    if bad:
        print("failed:", ", ".join(bad))
    return 0 if not bad else 1


# ---------------------------------------------------------------- main

def _emit(doc: dict, a) -> int:
    mode = "json" if a.json else ("verbose" if a.verbose else "line")
    if mode == "json":
        print(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    elif mode == "verbose":
        print(render_verbose(doc))
    else:
        print(doc.get("line") or build_line("OK", []))
    if a.out and doc.get("ok", False):
        p = pathlib.Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return 2 if (not doc.get("ok", False) or doc.get("status") == "WARN") else 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="文件级互斥闸（只读）：建卡/派发前检测共享文件冲突")
    ap.add_argument("--board", help="板 slug（默认由 HERMES_KANBAN_BOARD / HERMES_KANBAN_DB 推导）")
    ap.add_argument("--boards-root", help="看板根目录（默认 HERMES_KANBAN_DB 推导，再退 %%LOCALAPPDATA%%\\hermes\\kanban\\boards）")
    ap.add_argument("--db", help="直接给 kanban.db 路径（优先级最高）")
    ap.add_argument("--scope", default=",".join(DEFAULT_SCOPE), help="扫描状态（默认 running,ready,todo）")
    ap.add_argument("--card", help="候选卡 id：只报它与在队卡的冲突")
    ap.add_argument("--files", help="候选文件清单（逗号分隔）")
    ap.add_argument("--body-file", help="候选卡面正文文件（建卡前预检）")
    ap.add_argument("--json", action="store_true", help="单行 JSON（机读）")
    ap.add_argument("--line", action="store_true", help="只打一行 KANBAN_CONFLICT=（默认）")
    ap.add_argument("--verbose", action="store_true", help="人读：卡→文件清单 + 冲突 + 建议")
    ap.add_argument("--out", help="把 JSON 全量落盘到该路径（唯一写盘口）")
    ap.add_argument("--selftest", action="store_true", help="离线自检（合成板）")

    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()

    given = [x for x in (a.card, a.files, a.body_file) if x is not None]
    try:
        if len(given) > 1:
            raise GuardError("bad_input", "--card / --files / --body-file 互斥，只能给一个")
        slug, db = resolve_db(a)
        scope = tuple(s.strip() for s in str(a.scope).split(",") if s.strip())
        cand_kind = ("card" if a.card else "files" if a.files is not None else "body" if a.body_file else None)
        cand_value = a.card if a.card else (a.files if a.files is not None else a.body_file)
        doc = build_doc(db, slug, scope, cand_kind, cand_value)
    except GuardError as exc:
        board = a.db or a.board or "-"
        doc = error_doc(exc.kind, exc.detail, board, str(a.db or ""))
        rc = _emit(doc | {"line": f"KANBAN_CONFLICT=ERROR:{exc.kind} detail={exc.detail.replace(chr(10), ' ')[:200]}"}, a)
        return rc
    except Exception as exc:  # noqa: BLE001 —— 兜底：绝不抛裸异常（fail-closed 转 rc=2）
        doc = error_doc("internal", f"{type(exc).__name__}: {exc}")
        rc = _emit(doc | {"line": f"KANBAN_CONFLICT=ERROR:internal detail={doc['error']['detail'][:200]}"}, a)
        return rc
    return _emit(doc, a)


if __name__ == "__main__":
    sys.exit(main())
