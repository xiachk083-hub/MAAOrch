"""修改树：每关一条版本链（`v1_ai` → `v2_human` → `v3_human`，可分支 `v2b_human`）。

由来（2026-09-16 用户《作业系统 · 总指导》）：核心是**迭代** —— AI 生成 → 人改 → 对比 → 入库 → 反哺。
「对比」的原始素材就是这棵树：**每个节点记下"相对父版本改了什么"**（结构化 changes），
所以差异不需要事后重算，节点一落盘就已经是标注数据（`count` 越大规则越可靠，见 `tools/rules_db.py`）。

    <关卡目录>/versions/
      v1_ai.json         v1_ai.meta.json         ← AI 选配的那份（generate_stage 时自动建）
      v2_human.json      v2_human.meta.json      ← 人第一次改（parent = v1_ai）
      v2b_human.json     v2b_human.meta.json     ← 从 v1_ai 另开一支（同一父的第 2 个子节点）
      v3_human.json      v3_human.meta.json      ← 在 v2 之上继续改
    cleared.json                                 ← 打过关的那份（"标记为 cleared" 时同步）

三条口径：
  1. **版本文件与 meta 分开**：`.json` 是能直接喂 MAA 的作业，`.meta.json` 是溯源
     （父节点/作者/时间/changes/备注）。作业本身不含任何我们的字段，扔给 MAA 就能跑。
  2. **父节点可以是"非版本"**（某份候选作业）：人直接改候选时，节点仍是 `v2_human`，
     `base_src` 记下改的是哪份候选、`changes` 仍相对它算 —— 否则"改了哪几处"就丢了。
  3. **编号按深度 + 兄弟字母**：深度 = 父深度+1（父为候选时深度 2），同一父的第 N 个子节点
     依次用 `v2` / `v2b` / `v2c`。**不重排已有节点**（版本是历史，不能因为加了分支就改号）。
"""
from __future__ import annotations

import json
import pathlib
import re
import time

_HERE = pathlib.Path(__file__).resolve().parent
import sys   # noqa: E402
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import job_diff as JD          # noqa: E402

V_DIR = "versions"
_ID_RE = re.compile(r"^v(\d+)([a-z]*)_(\w+)$")


def _atomic(p, text: str) -> pathlib.Path:
    """原子写：版本文件写到一半被中断，比没有版本更坏（会被当成"改过但改坏了"）。"""
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
    return p


# ── 读写 ──

def vdir(sd) -> pathlib.Path:
    return pathlib.Path(sd) / V_DIR


def parse_id(vid: str) -> dict | None:
    m = _ID_RE.match(str(vid or ""))
    if not m:
        return None
    return {"num": int(m.group(1)), "letter": m.group(2), "author": m.group(3), "id": vid}


def load_tree(sd) -> list[dict]:
    """读这一关的全部版本节点（按 深度 → 兄弟字母 排序，不看文件时间）。"""
    out = []
    for f in sorted(vdir(sd).glob("*.meta.json")):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if m.get("id"):
            out.append(m)
    def key(m):
        p = parse_id(m["id"]) or {"num": 0, "letter": ""}
        return (p["num"], p["letter"])
    out.sort(key=key)
    return out


def version_path(sd, vid) -> pathlib.Path:
    """版本 id（`v2_human`）→ 作业文件路径；也接受直接给文件名。"""
    sd = pathlib.Path(sd)
    for cand in (sd / V_DIR / ("%s.json" % vid), sd / V_DIR / vid):
        if cand.is_file():
            return cand
    p = next(iter(sorted(vdir(sd).glob("%s*.json" % vid))), None)
    if p and not p.name.endswith(".meta.json"):
        return p
    raise SystemExit("没有这个版本：%s（这一关有 %s）"
                     % (vid, [m["id"] for m in load_tree(sd)] or "无"))


def _load_job(p) -> dict:
    d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    if isinstance(d, list):
        d = d[0] if d and isinstance(d[0], dict) else {}
    return d


# ── 编号 ──

def next_id(tree: list[dict], parent: str | None, author: str = "human") -> str:
    """下一个节点 id。

    **树为空 → `v1_<author>`（根）**；父为版本 → 深度 = 父深度+1；
    父为候选/None（但树里已有根）→ 深度 2（"这份候选的人改版"，与 v1 的孩子同层）。
    同一父的第 N 个子节点用 `v2` / `v2b` / `v2c`（第 1 个不带字母，与用户给的树一致），
    兄弟字母按**已有节点的最大字母**往后取，所以删掉中间某个节点也不会撞号。
    """
    if not tree:
        return "v1_%s" % author
    p = parse_id(parent) if parent else None
    depth = (p["num"] + 1) if p else 2
    letters = []
    for m in tree:
        q = parse_id(m.get("id"))
        if not q or q["num"] != depth:
            continue
        if (m.get("parent") or None) == (parent or None):
            letters.append(q["letter"])
    if not letters:
        letter = ""
    else:
        used = {x for x in letters}
        letter = ""
        i = 0
        while letter in used:                       # "" → b → c …
            i += 1
            letter = chr(ord("a") + i)
    return "v%d%s_%s" % (depth, letter, author)


# ── 写 ──

def add_version(sd, job: dict, author: str = "human", parent: str | None = None,
                base_src: str = "", note: str = "", mark_cleared: bool = False,
                stage_id: str = "", account: str = "", activity: str = "", code: str = "",
                quiet: bool = False) -> dict:
    """落一个版本节点。返回节点 dict；`.meta.json` 里的 `changes` 是**相对父版本**的结构化差异。

    `parent`：版本 id（`v1_ai`）或 None（父是候选作业，用 `base_src` 记是哪一份，路径**相对关卡目录**）。
    父作业读不到时（文件被清掉 / 路径给错）**changes 记为空并标 `parent_missing`**，不假装"没改过"。
    """
    sd = pathlib.Path(sd)
    (sd / V_DIR).mkdir(parents=True, exist_ok=True)
    tree = load_tree(sd)
    vid = next_id(tree, parent, author)

    changes, parent_missing = [], False
    parent_job = None
    if parent:
        try:
            parent_job = _load_job(version_path(sd, parent))
        except SystemExit:
            parent_missing = True
    elif base_src:
        p = pathlib.Path(base_src)
        cand = p if p.is_absolute() else (sd / p)
        if cand.is_file():
            try:
                parent_job = _load_job(cand)
            except Exception:
                parent_missing = True
        else:
            parent_missing = True
    if parent_job:
        changes = JD.diff_jobs(parent_job, job)

    job_out = dict(job)
    if stage_id:
        job_out["stage_name"] = stage_id        # 存错关卡会让跨账号比对整片错位
    f = vdir(sd) / ("%s.json" % vid)
    _atomic(f, json.dumps(job_out, ensure_ascii=False, indent=1))
    meta = {"id": vid, "file": f.name, "parent": parent or None, "base_src": base_src or "",
            "author": author, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stage_id": stage_id, "account": account, "activity": activity, "code": code,
            "n_actions": len(job.get("actions") or []),
            "n_changes": len(changes), "changes": changes,
            "summary": JD.summarize(changes),
            "opers": [o.get("name") for o in (job.get("opers") or []) if isinstance(o, dict)],
            "note": (note or "").strip(), "mark_cleared": bool(mark_cleared),
            "parent_missing": parent_missing}
    _atomic(vdir(sd) / ("%s.meta.json" % vid),
            json.dumps(meta, ensure_ascii=False, indent=1))

    if mark_cleared:
        _atomic(sd / "cleared.json", json.dumps(job_out, ensure_ascii=False, indent=1))
        _cleared_meta(sd, vid, account, activity, stage_id, code, note, len(changes))
    if not quiet:
        print("   版本 %s（%s，改 %d 处%s）%s" % (vid, author, len(changes),
                                              "，父缺失" if parent_missing else "",
                                              ("｜" + meta["note"][:30]) if meta["note"] else ""))
    return meta


def _cleared_meta(sd, vid, account, activity, stage_id, code, note, n_changes):
    _atomic(sd / "cleared_meta.json", json.dumps(
        {"account": account, "activity": activity, "stage_id": stage_id, "code": code,
         "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "version": vid,
         "n_changes": n_changes, "note": (note or "").strip()},
        ensure_ascii=False, indent=1))


def ensure_v1_ai(sd, job: dict | None, top: dict | None, st: dict,
                 account: str = "", activity: str = "", quiet: bool = False) -> dict | None:
    """建树的根 `v1_ai` = 匹配引擎排第一的那份候选（AI 的选配）。

    已有版本就不动 —— **版本是历史，重生成工作区不该改写它**。没有候选 / 读不到作业时返回 None
    （这一关暂时没有 AI 版本，不是错误）。
    """
    if load_tree(sd) or not job:
        return None
    top = top or {}
    missing = top.get("missing") or []
    note = "AI 选配：%s%s" % (top.get("match") or "?",
                             ("，缺 %s" % "、".join(missing)) if missing else "")
    return add_version(sd, job, author="ai", parent=None,
                       base_src=(("jobs/%s" % top["saved_as"]) if top.get("saved_as") else ""),
                       note=note, stage_id=st.get("stage_id", ""), account=account,
                       activity=activity, code=st.get("code", ""), quiet=quiet)


# ── 查 / 比 ──

def current(sd) -> dict | None:
    """当前"最新"节点 = 深度最大的那个（同深度取最后一个兄弟）。"""
    t = load_tree(sd)
    return t[-1] if t else None


def version_diff(sd, a: str, b: str) -> list[dict]:
    """两个版本之间的结构化差异（`a` → `b`）。"""
    ja = _load_job(version_path(sd, a))
    jb = _load_job(version_path(sd, b))
    return JD.diff_jobs(ja, jb)


def chain(sd, vid: str) -> list[dict]:
    """从根到 `vid` 的链（含端点），用于"点开看修改链"。"""
    tree = {m["id"]: m for m in load_tree(sd)}
    out, cur, seen = [], vid, set()
    while cur and cur in tree and cur not in seen:
        seen.add(cur)
        out.append(tree[cur])
        cur = tree[cur].get("parent")
    return list(reversed(out))


def tree_summary(sd) -> dict:
    """给索引页 / 编辑器用的紧凑摘要（changes 全带上：面板要显示"改了哪几处"）。"""
    t = load_tree(sd)
    return {"n": len(t), "cleared_exists": (pathlib.Path(sd) / "cleared.json").is_file(),
            "nodes": [{"id": m["id"], "file": m["file"], "parent": m.get("parent"),
                       "author": m.get("author"), "at": m.get("at"),
                       "n_changes": m.get("n_changes", 0), "note": m.get("note") or "",
                       "mark_cleared": bool(m.get("mark_cleared")),
                       "changes": m.get("changes") or [],
                       "summary": m.get("summary") or {}} for m in t]}
