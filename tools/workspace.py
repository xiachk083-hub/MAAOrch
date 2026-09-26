"""工作区：按「账号 × 活动 × 关卡」组织候选作业与最终通关（一次生成整个活动）。

由来（2026-09-16 用户）：以前是一关一关生成，选完账号还得挑关；现在选完账号+活动，
一次把整个活动铺开，每关带「候选作业 + 最终通关 + 备注」，点关卡就进编辑器。

目录结构（`data/workspaces/` 在 .gitignore 里 —— 里面有账号 box，绝不入库）：

    data/workspaces/
      index.html                       工作区根主页（账号列表）
      <account>/
        box.json                       生成时的那份干员箱快照（回溯用：结论只对这份 box 成立）
        index.html                     活动列表
        <activity>/
          index.html                   活动主页：关卡 × [N 份作业] × [已通关]
          <CODE>/                      目录名用关卡代号（SR-1），不是 stageId（act54side_01）
            stage.json                 {stage_id, code, name, group, level_data…}
            index.html                 这一关的编辑器页（viz_plan 的 HTML + 工作区导航）
            jobs/*.json                候选作业（匹配引擎排序后挑进来的，原文件字节复制）
            manifest.json              全部候选的匹配结论（挑进来的与被淘汰的都留档）
            cleared.json               人改完保存的最终通关（MAA 作业格式，可直接跑）
            cleared_meta.json          谁在什么时候从哪份候选存下来的（溯源，不属于作业本身）
            notes.md                   人工备注：为什么改、改了哪里

三条设计口径（不是没做完，是定的）：
  1. **目录名用关卡代号**（`SR-1`）、`stage.json` 里才存 `stage_id`（`act54side_01`）。
     人看的是代号，机器要的是 stageId —— 两者都留，谁也别猜谁。
  2. **候选作业排除自产件**（`ga_derived/`、`*_adapted`、doc.title 含「编排」）——
     拿自己的半成品当候选推荐给自己是错的（`tools/batch_index.py` 的既有口径）。
     想看自产件用 `--include-derived`。
  3. **缺关卡数据就去拉**（镜像 → `data/_level_<stage>.json`，与 `stage_value.load_level` 同源）。
     拉不到就生成一个「无地图」的简化关卡页：作业清单照旧可看，只有地图没了 ——
     整活动不会因为一关没数据而整体失败。
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import re
import shutil
import sys
import time
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))          # tools/ —— 让 viz_plan / batch_index 能被 flat import
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))       # 仓库根 —— tools.xxx 包式 import 的调用方也吃得住

WS = PROJ / "data" / "workspaces"
BOX_DIR = PROJ / "data" / "operbox_batch"
DEFAULT_BOX = BOX_DIR / "emu2.json"

# 活动展示名。**不猜**：没有登记的就把 activity id 原样显示（宁可显示 act16side，
# 也不编一个中文名）。要补就往这里加，或写 data/workspaces/activities.json：
#   {"act54side": "月行水上"}
ACTIVITY_NAMES = {"act54side": "月行水上"}

# 关卡数据镜像（与 tools/stage_value.py 的 BASES 同源；那里 import 了 cv2/numpy，
# 只为三个 URL 不值当，所以这里留一份并标注同源 —— 改一处记得改两处）
LEVEL_BASES = [
    "https://ghproxy.net/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/",
    "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/",
    "https://gcore.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/",
]
UA = {"User-Agent": "Mozilla/5.0"}

GROUPS = {"main": 0, "ex": 1, "tr": 2, "st": 3}
GROUP_LABEL = {"main": "主线", "ex": "EX", "tr": "教学", "st": "剧情"}
# 一次生成几关算"整个活动"：活动关 16~20 关属正常，`main` 这类前缀会命中 268 关 —— 那是误操作
MAX_AUTO_STAGES = 40


# ── 小工具 ──

def _atomic_write(p, text: str) -> pathlib.Path:
    """原子写（同目录 .tmp → replace）：半个文件比没有文件更坏。"""
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
    return p


def _natural(s: str):
    """SR-2 排在 SR-10 前面（字符串排序会把 SR-10 排到 SR-2 前）。"""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def _safe_name(s: str) -> str:
    """账户/活动/or 代号进路径前必须过这一关：只允许字母数字._-，且不允许 .. """
    s = str(s)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", s) or s in (".", ".."):
        raise ValueError("非法名称（只允许字母数字._-）：%r" % s)
    return s


def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", str(s or "")).strip("_")
    return s[:n] or "job"


# ── 关卡表 ──

_ST = None


def stage_table() -> dict:
    global _ST
    if _ST is None:
        _ST = json.loads((PROJ / "data" / "_stage_table.json").read_text(encoding="utf-8"))["stages"]
    return _ST


def stage_meta(stage_id: str) -> dict:
    v = stage_table().get(stage_id) or {}
    return {"stage_id": stage_id, "code": v.get("code") or stage_id, "name": v.get("name") or "",
            "level_id": v.get("levelId"), "hard_id": v.get("hardStagedId"),
            "type": v.get("stageType"), "ap": v.get("apCost"),
            "difficulty": v.get("difficulty")}


def stage_group(stage_id: str) -> str:
    sid = stage_id
    if "_ex" in sid:
        return "ex"
    if "_tr" in sid:
        return "tr"
    if "_st" in sid:
        return "st"
    return "main"


def activity_stages(activity: str, include_nonbattle: bool = False) -> list[dict]:
    """活动的关卡清单（顺序：主线 → EX → 教学 → 剧情；关内按代号自然序）。

    过滤掉 `#f#`（突袭变体：同一个 stageId 的第四难度，副本共用关卡数据，
    在这里当成独立关卡只会让人看到两份一样的图）；突袭信息挂在主线那条的 has_hard 上。
    """
    activity = _safe_name(activity)
    out = []
    for sid in stage_table():
        if "#" in sid or not sid.startswith(activity + "_"):
            continue
        if any(sid.startswith(activity + "_" + t) for t in ("st",)):        # 纯剧情关，无战斗
            grp = "st"
        else:
            grp = stage_group(sid)
        if grp in ("st", "tr") and not include_nonbattle:
            continue
        m = stage_meta(sid)
        m["group"] = grp
        m["has_hard"] = bool(m.get("hard_id"))
        out.append(m)
    out.sort(key=lambda x: (GROUPS.get(x["group"], 9), _natural(x["code"])))
    return out


def activity_name(activity: str) -> str:
    extra = {}
    f = WS / "activities.json"
    if f.is_file():
        try:
            extra = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            extra = {}
    return extra.get(activity) or ACTIVITY_NAMES.get(activity) or activity


def recent_activities(n: int = 8) -> list[str]:
    """池子里最近出现过的**活动** id（给 CLI 提示"现在该生成哪个"）。

    只收 `act<数字>…` 形态：主线关的 stage_name 是 `main_00-01`，取前缀会得到 `main`
    并报出"268 关"——那不是活动，把它当 --activity 传进去就是一次 268 关的误生成。
    """
    cnt = collections.Counter()
    for e in pool_index():
        a = e["stage"].rsplit("_", 1)[0]
        if re.match(r"^act\d+[a-z]*$", a):
            cnt[a] = max(cnt[a], e["mtime"])
    return [a for a, _ in cnt.most_common(n)]


# ── 作业池 ──

_POOL = None


def pool_index(rebuild: bool = False) -> list[dict]:
    """作业池全量索引（319 份 json，glob 一次 0.01s，所以一次生成只建一次）。

    用一个完整索引而不是 `viz_plan.scan_jobs()` 的文件名前缀扫描：**作业自带的
    `stage_name` 才是权威**（前缀扫描要求文件名以 `<stage>_` 开头，池子里
    `event54_picked/act54side_01_101884.json` 恰好满足、但换个命名就漏）。
    """
    global _POOL
    if _POOL is not None and not rebuild:
        return _POOL
    from tools.batch_index import is_valid_job, _is_derived, _self_made_name
    out, n_container = [], 0
    for f in glob.glob(str(PROJ / "data" / "copilot" / "**" / "*.json"), recursive=True):
        p = pathlib.Path(f)
        if _self_made_name(f):
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(j, list):
            n_container += 1     # 容器不是作业：取首元素会把"20 份 dump 的第 1 份"冒充成这一份
            continue
        if not isinstance(j, dict) or "opers" not in j or not is_valid_job(j):
            continue
        st = j.get("stage_name")
        if not st:
            continue            # 没声明关卡的作业进不了任何一关的候选（不猜它属于哪关）
        title = ((j.get("doc") or {}).get("title") or "") if isinstance(j.get("doc"), dict) else ""
        out.append({"path": p, "file": str(p.relative_to(PROJ)).replace("\\", "/"),
                    "stage": st, "id": _job_id(p), "title": title, "job": j,
                    "derived": bool(_is_derived(f, j)),
                    "mtime": p.stat().st_mtime})
    _POOL = out
    if n_container:
        print("   （池子里 %d 个数组文件（作业集/dump）未纳入候选）" % n_container)
    return out


_MODE_FLAGS = None


def mode_flags() -> dict:
    """作业 id → 模式标志（`raid` / `raid_only` / `normal` / `normal_only`）。

    来源 = **项目自己的筛选产物**（`data/copilot/*screening*.json`、`*pick*.json`，
    由 `tools/prts_event_pick.py` 按作业站的字段判定），不是我这边的关键词猜测。
    实测 536 个 id 有标志、其中 24 个 `raid_only`。

    为什么必须有这个：**突袭作业和普通作业在作业站上声明同一个 `stage_name`**
    （实测 act54side_ex04 的「SR-EX-4 突袭挂机作战」也写 act54side_ex04）⇒ 只按 stage_name
    匹配根本分不开，AI 会把突袭的轴选给普通关（实测 9036/SR-EX-4 就中招了）。
    """
    global _MODE_FLAGS
    if _MODE_FLAGS is not None:
        return _MODE_FLAGS
    out = {}
    for f in sorted(glob.glob(str(PROJ / "data" / "copilot" / "*screening*.json"))) +              sorted(glob.glob(str(PROJ / "data" / "copilot" / "*pick*.json"))):
        try:
            d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue

        def walk(o):
            if isinstance(o, dict):
                if o.get("id") and ("raid" in o or "raid_only" in o):
                    cur = out.setdefault(str(o["id"]), {})
                    for k in ("raid", "raid_only", "normal", "normal_only"):
                        if k in o:
                            cur[k] = bool(cur.get(k)) or bool(o[k])
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(d)
    _MODE_FLAGS = out
    return out


def raid_only(job_id: str) -> bool:
    """这份作业是不是**仅突袭**（普通关不该用它当候选）。无标志 → False（不猜）。"""
    f = mode_flags().get(str(job_id)) or {}
    return bool(f.get("raid_only")) or (bool(f.get("raid")) and not f.get("normal"))


def raid_suspect(title: str) -> bool:
    """标题疑似突袭 —— **只用来标注，不用来过滤**（关键词会有假阳性，除非没有权威标志）。"""
    t = title or ""
    return ("突袭" in t) or ("磨难" in t)


def _job_id(p: pathlib.Path) -> str:
    """作业身份 = prts 数字 id（`act54side_01_101906.json` → `101906`），没有就用文件名。"""
    m = re.search(r"_(\d{4,7})(?:_|$)", p.stem)
    return m.group(1) if m else p.stem


# ── box ──

def box_path(account: str) -> pathlib.Path | None:
    """账号 → box 文件。两种命名都认：`<account>.json` 与 `emu2_<account>.json`。"""
    account = _safe_name(account)
    for c in (BOX_DIR / ("%s.json" % account), BOX_DIR / ("emu2_%s.json" % account)):
        if c.is_file():
            return c
    return None


def load_account_box(account: str):
    """(box 解析结果, 原始文件路径)。box 缺失 → 明确抛错，绝不静默当成"拥有 0 人"。

    `match_candidates.build_box_index` 踩过这个坑（0 人 → 所有作业都被判全员缺失），
    这里直接不带默认值：宁可生成失败，也不产出一份"全员缺"的假工作区。
    """
    p = box_path(account)
    if not p:
        raise SystemExit("找不到账号 %s 的 box：%s 下没有 %s.json / emu2_%s.json"
                         % (account, BOX_DIR, account, account))
    raw = json.loads(p.read_text(encoding="utf-8"))
    from tools.match_candidates import build_box_index
    box = build_box_index(raw)
    if not box["owned_names"]:
        raise SystemExit("box %s 解析出 0 名干员（schema 不符？）—— 不生成工作区" % p)
    return box, p


# ── 关卡数据 ──

def ensure_level(stage_id: str, fetch: bool = True) -> tuple[pathlib.Path | None, str]:
    """确保 `data/_level_<stage>.json` 在位。返回 (路径|None, 状态)。

    镜像路径要把 levelId **整串小写**（`Activities/act54side/…` → `activities/act54side/…`，
    实测大写一律 404，主线 `Obt/Main/…` 同理）。先试原样再试小写，别只试一种。
    """
    p = PROJ / "data" / ("_level_%s.json" % stage_id)
    if p.is_file() and p.stat().st_size > 1000:
        return p, "cached"
    if not fetch:
        return None, "missing"
    lid = stage_meta(stage_id).get("level_id")
    if not lid:
        return None, "no_level_id"
    cands = [lid] + ([lid.lower()] if lid.lower() != lid else [])
    for c in cands:
        for base in LEVEL_BASES:
            try:
                raw = urllib.request.urlopen(
                    urllib.request.Request("%slevels/%s.json" % (base, c), headers=UA),
                    timeout=60).read()
                if len(raw) < 1000:
                    continue
                _atomic_write(p, raw.decode("utf-8"))
                return p, "fetched"
            except Exception:
                continue
    return None, "download_failed"


# ── 候选匹配 ──

def _content_key(j: dict) -> str:
    """作业身份指纹 = (actions, opers, groups) 的哈希。

    池子里同一份作业会被下到不同目录（实测 `event54_d/act54side_ex05_102615.json` 与
    `event54_picked/…` 字节相同），只按 prts id 或文件名去重会**漏**（同一个 id 在不同
    目录下文件名前缀不同），结果同一份作业在候选里出现两次、进 jobs/ 还会互相覆盖。
    """
    import hashlib
    blob = json.dumps([j.get("actions"), j.get("opers"), j.get("groups")],
                      ensure_ascii=False, sort_keys=True)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:10]


def _lvl_import():
    import viz_plan as _VP
    return _VP.level


VP_level = None


def candidates_for(account_box: dict, stage_id: str, include_derived: bool = False,
                   limit: int = 6, account: str = "", use_group_cache: bool = True,
                   quiet_pool: bool = False) -> tuple[list[dict], list[dict]]:
    """返回 (挑中的候选, 全部候选的匹配结论)。

    排序口径 = 匹配引擎的 (缺人, 练度缺, 软需求) 三级，再让自产件/低配件靠后。
    引擎自带的 `allow_low_squad=False` 会把需求 <6 的作业直接丢掉 —— 但 SR 这类活动关
    本来就少人（首选作业只有 2 个干员），丢掉的往往正是最该看的那几份，所以这里开 True
    并在结论里标 `low_squad`，让排序去压它，而不是让它消失。

    `account` 给了就用**同组账号的候选顺序**做池子遍历顺序（见 tools/box_groups.py）：
    这只影响"排序键完全相同的作业之间谁在前"，**不改变任何一条判定**（缺人/练度仍按
    这个号自己算）。同组 ≠ 同一个号，把结论也共享就会静默放过练度不够的号。
    """
    global VP_level
    if VP_level is None:
        VP_level = _lvl_import()
    from tools.match_candidates import rank_candidates, compute_missing_detailed
    want_raid = stage_id.endswith("#f#")
    pool, n_raid = [], 0
    for e in pool_index():
        if e["stage"] != stage_id or (e["derived"] and not include_derived):
            continue
        if not want_raid and raid_only(e["id"]):
            n_raid += 1
            continue
        pool.append(e)
    if n_raid and not want_raid:
        print("   （按模式剔掉 %d 份**仅突袭**作业：它们声明同一个 stage_name，选给普通关会用错模式）" % n_raid)
    # **结构体检**：部署落在虚空/图外的作业不可能打得过（实测 GA-EX-2 的 v1_ai 整份只有 1 个部署，
    # 还落在整列都是墙的第 3 列）。这类不是"打得不好"，是"结构上不可能"，不该进候选表 ——
    # 与"仅突袭"同族：先把不可能执行的剔掉，再谈选优。
    n_invalid = 0
    lvl = None
    try:
        lvl = VP_level(stage_id)
    except Exception:
        lvl = None
    if lvl:
        Wd, Hd, key = lvl[0], lvl[1], lvl[2]

        def _bad(job):
            for a in (job.get("actions") or []):
                if a.get("type") not in ("Deploy", "部署") or not a.get("location"):
                    continue
                x, y = a["location"]
                if not (0 <= x < Wd and 0 <= y < Hd):
                    return "部署越界 [%d,%d]" % (x, y)
                if key(x, y) == "tile_forbidden":
                    return "部署在虚空 [%d,%d]" % (x, y)
            return None

        kept_pool = []
        for e in pool:
            why = _bad(e["job"])
            if why:
                n_invalid += 1
                if not quiet_pool:
                    print("   （剔掉结构上不可能的作业 %s：%s）" % (e["id"], why))
                continue
            kept_pool.append(e)
        pool = kept_pool
    if not pool:
        return [], []
    order = None
    if account and use_group_cache:
        try:
            import box_groups as BG
            order = BG.get_cached_order(stage_id, account)
        except Exception:
            order = None
    if order:
        rank = {jid: i for i, jid in enumerate(order)}
        pool.sort(key=lambda e: (rank.get(e["id"], 10 ** 6), e["file"]))
    raws = []
    for e in pool:
        j = dict(e["job"])
        j["_file"] = str(e["path"])
        raws.append(j)
    ranked = rank_candidates(account_box, raws, allow_low_squad=True)
    by_stem = {e["path"].stem: e for e in pool}
    by_id = {}
    for e in sorted(pool, key=lambda x: x["file"]):      # 同 id 多文件时固定取路径最小的那份
        by_id.setdefault(e["id"], e)
    rows, seen = [], {}
    for r in ranked:
        # rank_candidates 的 job_id 是**文件 stem**（不是数字 id），所以先按 stem 认领
        e = by_stem.get(r["job_id"]) or by_id.get(r["job_id"])
        if not e:
            continue
        ck = (stage_id, _content_key(e["job"]))
        if ck in seen:
            seen[ck].setdefault("duplicates", []).append(e["file"])
            continue
        det = compute_missing_detailed(e["job"], account_box)
        r.update({"id": e["id"], "file": e["file"], "path": str(e["path"]), "title": e["title"],
                  "stage": stage_id, "derived": e["derived"],
                  "low_squad": r["n_reqs"] < 6,
                  "missing_detail": det, "n_detail": len(det)})
        rows.append(r)
        seen[ck] = r
    rows.sort(key=lambda x: (x["n_missing"], x["n_detail"], x["n_soft"],
                             1 if x["derived"] else 0, 1 if x["low_squad"] else 0,
                             -x["n_actions"]))
    ok = [r for r in rows if r["match"] in ("exact", "partial")]
    if not ok:                      # 全都不匹配也别交白卷：把最接近的几份放进去并标明
        ok = [dict(r, fallback=True) for r in rows[:max(1, min(3, len(rows)))]]
    return ok[:limit], rows


# ── 生成 ──

def stage_dir(account: str, activity: str, code: str) -> pathlib.Path:
    return WS / _safe_name(account) / _safe_name(activity) / _safe_name(code)


def find_stage_dir(account: str, activity: str, key: str) -> pathlib.Path | None:
    """按代号（SR-1）**或** stageId（act54side_01）找关卡目录。"""
    base = WS / _safe_name(account) / _safe_name(activity)
    for d in sorted(base.glob("*/")):
        sj = d / "stage.json"
        if d.name == key:
            return d
        if sj.is_file():
            try:
                if (json.loads(sj.read_text(encoding="utf-8")) or {}).get("stage_id") == key:
                    return d
            except Exception:
                pass
    return None


def _box_group(account: str) -> str | None:
    """这个号属于哪个 box 组（没分过组返回 None —— 不猜）。"""
    try:
        import box_groups as BG
        return BG.group_of(account)
    except Exception:
        return None


def _job_files_from_manifest(sd: pathlib.Path) -> list[pathlib.Path]:
    """下拉里的候选顺序：**按匹配排名**（AI 最推荐的在前），不看文件名。

    以前是 `sorted(glob)` —— 字母序，于是打开一关默认显示的是"文件名最小的那份"，
    而 AI 的根节点取的是排名第一的那份（实测 SR-2：下拉默认 101749、根节点是 101766），
    人一进页面看到的跟"AI 给的"对不上。排名从 manifest 的 candidates 出。
    """
    jobs = list((sd / "jobs").glob("*.json"))
    order = {}
    mf = sd / "manifest.json"
    if mf.is_file():
        try:
            rows = json.loads(mf.read_text(encoding="utf-8")).get("candidates") or []
            for i, row in enumerate(rows):
                if row.get("saved_as"):
                    order[row["saved_as"]] = i
        except Exception:
            order = {}
    jobs.sort(key=lambda p: (order.get(p.name, 10 ** 6), p.name))
    return jobs


def _page_job_files(sd: pathlib.Path) -> list[pathlib.Path]:
    """编辑器下拉的完整顺序：人存的通关版 → AI 的根版本 → 候选（按排名）。

    AI 的根版本（`v1_ai`）排在候选前面：它就是"AI 给的那一份"，默认该显示它，
    而不是同内容的那条候选文件（两条都在，一条是**版本节点**、一条是池子原文件）。
    """
    import versions as VSN
    out = []
    cl = sd / "cleared.json"
    if cl.is_file():
        out.append(cl)
    for m in [x for x in VSN.load_tree(sd) if not x.get("parent") and x.get("author") == "ai"]:
        p = sd / VSN.V_DIR / m["file"]
        if p.is_file():
            out.append(p)
    out += _job_files_from_manifest(sd)
    return out


def _seed_ai_version(sd: pathlib.Path, kept: list[dict], st: dict,
                     account: str, activity: str, quiet: bool) -> None:
    """把 AI 选配的那份候选落成修改树的根 `v1_ai`（只在还没有任何版本时做）。

    树的根必须在这里建：人改完保存时要把"改了什么"相对**AI 原本给的那份**算出来。
    没有这一步，第一个 human 节点就只能对着候选算，链上少了 AI 这一环。
    """
    import versions as VSN
    if not kept or VSN.load_tree(sd):
        return
    f = sd / "jobs" / (kept[0].get("saved_as") or "")
    if not f.is_file():
        return
    try:
        job = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(job, list):
        job = job[0] if job and isinstance(job[0], dict) else {}
    VSN.ensure_v1_ai(sd, job, kept[0], st, account, activity, quiet=quiet)


def generate_stage(account: str, activity: str, st: dict, account_box: dict, box_file,
                   limit: int = 6, include_derived: bool = False, fetch_levels: bool = True,
                   quiet: bool = False) -> dict:
    """生成一关：候选作业落盘 + 编辑器页。返回这一关的摘要（供活动主页与 CLI 汇报）。"""
    code = st["code"]
    sid = st["stage_id"]
    sd = stage_dir(account, activity, code)
    (sd / "jobs").mkdir(parents=True, exist_ok=True)

    picked, rows = candidates_for(account_box, sid, include_derived, limit, account=account)
    # 候选作业原样字节复制：不做任何"顺手修正"，页面上看到的就是池子里那一份
    kept = []
    for r in picked:
        name = pathlib.Path(r["path"]).name
        if not name.startswith(sid):
            name = "%s_%s" % (sid, r["id"] and (r["id"] + ".json") or name)
        dest = sd / "jobs" / name
        dest.write_bytes(pathlib.Path(r["path"]).read_bytes())
        kept.append(dict(r, saved_as=dest.name))
    # jobs/ 是**机器拥有**的目录：重生成时把上一轮遗留的清掉，否则换了候选口径后
    # 旧候选还留在下拉里，跟新候选混着看（cleared.json / notes.md 是人的产出，永不动）
    keep_names = {k["saved_as"] for k in kept}
    stale = [f for f in (sd / "jobs").glob("*.json") if f.name not in keep_names]
    for f in stale:
        f.unlink()
        if not quiet:
            print("      （清掉上一轮遗留的候选 %s）" % f.name)
    _seed_ai_version(sd, kept, st, account, activity, quiet)

    lv_path, lv_status = ensure_level(sid, fetch_levels)
    page, page_kind, page_note = None, "editor", ""
    if lv_path:
        try:
            page = _write_editor_page(account, activity, st, sd, box_file)
        except Exception as e:              # 生成失败要留痕，不能静默掉成"没有这一关"
            page_kind, page_note = "error", "%s: %s" % (type(e).__name__, e)
    else:
        page_kind, page_note = "no_level", _LEVEL_WHY.get(lv_status, lv_status)

    # 已有的 cleared.json / notes.md 不能被重新生成冲掉（那是人的产出）
    cleared = (sd / "cleared.json").is_file()
    notes_f = sd / "notes.md"
    if not notes_f.is_file():
        _atomic_write(notes_f, "# %s %s 备注\n\n（为什么改、改了哪里；同一关不同账号的 cleared.json 放一起就是差异数据）\n" % (code, st.get("name") or ""))
    if page_kind != "editor" or not (sd / "index.html").is_file():
        _write_simple_page(account, activity, st, sd, kept, page_kind, page_note)

    manifest = {"stage_id": sid, "code": code, "name": st.get("name"), "group": st.get("group"),
                "has_hard": st.get("has_hard"), "account": account, "activity": activity,
                "box_file": str(box_file), "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "level_data": (str(lv_path.relative_to(PROJ)).replace("\\", "/") if lv_path else None),
                "level_status": lv_status, "include_derived": include_derived,
                "pool_total": len(rows), "picked": [r["id"] for r in kept],
                "raid_filtered": sum(1 for e in pool_index()
                                     if e["stage"] == sid and raid_only(e["id"])),
                "box_group": _box_group(account),
                "candidates": rows}
    _atomic_write(sd / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))

    stj = dict(st)
    stj.update({"account": account, "activity": activity, "activity_name": activity_name(activity),
                "generated_at": manifest["generated_at"], "level_status": lv_status})
    _atomic_write(sd / "stage.json", json.dumps(stj, ensure_ascii=False, indent=1))

    if not quiet:
        best = rows[0] if rows else None
        print("  %-9s %-6s 候选 %2d/%2d 份 | %s%s | 关卡数据 %s" % (
            code, st.get("name") or "", len(kept), len(rows),
            ("已通关 ✓" if cleared else "未通关"),
            (" | 最好: 缺%d %s" % (best["n_missing"], best["id"])) if best else " | 池子里没有这一关的作业",
            lv_status))
    import versions as VSN
    vs = VSN.tree_summary(sd)
    return {"stage_id": sid, "code": code, "name": st.get("name"), "group": st.get("group"),
            "dir": str(sd), "n_jobs": len(kept), "pool_total": len(rows),
            "cleared": cleared, "page_kind": page_kind, "page_note": page_note,
            "n_versions": vs["n"],
            "n_human_changes": sum(n.get("n_changes", 0) for n in vs["nodes"]
                                   if n.get("author") == "human"),
            "level_status": lv_status,
            "best": ({"id": rows[0]["id"], "match": rows[0]["match"],
                      "n_missing": rows[0]["n_missing"],
                      "missing": rows[0]["missing"][:4]} if rows else None)}


def _write_editor_page(account: str, activity: str, st: dict, sd: pathlib.Path, box_file):
    """这一关的编辑器页 = viz_plan 的 HTML + 工作区导航（ws 字段驱动）。

    **cleared.json 排在下拉第一位**（`下次直接用`）：人改完存下来的那份就是这一关的
    当前答案，重开页面还从原始候选起手会把人的工作藏起来；它按 src（`…/cleared.json`）
    单独占一个编辑槽，不会跟候选串位。
    """
    import viz_plan as VP
    sid = st["stage_id"]
    files = _page_job_files(sd)
    if not files:
        raise RuntimeError("没有候选作业，跳过编辑器页")
    prev_box = VP.BOX_PATH
    try:
        VP.BOX_PATH = pathlib.Path(box_file)      # 射程/费用/决斗全按**这个账号**的 box 算
        jobs, skipped = VP.load_jobs(files, VP.professions(), sid)
        if not jobs:
            raise RuntimeError("候选作业全无 actions")
        lv = VP.level(sid)
        data = VP.build_payload(sid, lv, jobs, box_path=box_file)
    finally:
        VP.BOX_PATH = prev_box
    data["ws"] = _ws_payload(account, activity, st, sd, len(jobs))
    p = VP.write_html(data, sd / "index.html")
    return p


def _ws_payload(account: str, activity: str, st: dict, sd: pathlib.Path, n_jobs: int) -> dict:
    """前端要的工作区上下文：面包屑、返回链接、账号/活动下拉的跳转目标、保存接口、版本链。"""
    import versions as VSN
    up = "../../.."
    accounts = [{"id": a, "href": "%s/%s/index.html" % (up, a)} for a in list_accounts()]
    acts = [{"id": a, "name": activity_name(a), "href": "%s/%s/%s/index.html" % (up, account, a)}
            for a in list_activities(account)]
    notes = ""
    nf = sd / "notes.md"
    if nf.is_file():
        notes = nf.read_text(encoding="utf-8")
    return {"account": account, "activity": activity, "activity_name": activity_name(activity),
            "stage": st["stage_id"], "code": st["code"], "name": st.get("name"),
            "activity_url": "../index.html", "root_url": "../../../index.html",
            "accounts": accounts, "activities": acts, "n_jobs": n_jobs,
            "cleared": (sd / "cleared.json").is_file(), "notes": notes,
            "save_url": "/api/ws/save", "notes_url": "/api/ws/notes",
            "version_url": "/api/ws/version", "versions_dir": VSN.V_DIR + "/",
            "versions": VSN.tree_summary(sd), "rel_prefix": up}


_LEVEL_WHY = {"missing": "本地没有关卡数据（--no-fetch）", "no_level_id": "关卡表里没有 levelId",
              "download_failed": "镜像拉取失败（网络/限流）"}


def _write_simple_page(account: str, activity: str, st: dict, sd: pathlib.Path,
                       kept: list[dict], kind: str, note: str) -> None:
    """没有地图数据（或生成报错）时的兜底页：作业清单照旧能看，别让整活动卡在一关。

    **不做假的编辑器**：没有 mapData 就画不出格子和路径，硬套一个空地图比明说更坏。
    """
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>缺 %d（%s）</td><td>%d 动作</td></tr>" % (
            r["id"], pathlib.Path(r["file"]).name, r.get("action") or "",
            r["n_missing"], "、".join(r["missing"][:3]) or "无",
            r["n_actions"]) for r in kept)
    title = "%s %s" % (st["code"], st.get("name") or "")
    why = ("关卡数据缺失：%s —— 先补 `data/_level_%s.json`（`python tools/workspace.py "
           "--account %s --activity %s --stage %s` 会再试一次拉取）" % (note, st["stage_id"],
                                                                        account, activity, st["stage_id"])) \
        if kind == "no_level" else ("生成编辑器页时报错：%s（作业已落盘，可直接跑 viz_plan 手工画）" % note)
    _atomic_write(sd / "index.html", """<!doctype html><meta charset="utf-8"><title>%s</title>
<style>body{background:#161a1e;color:#d8dee6;font:14px/1.6 system-ui,"Microsoft YaHei";padding:20px}
a{color:#8ac}table{border-collapse:collapse;margin-top:10px}td,th{border:1px solid #2c3138;padding:5px 10px;font-size:13px}
.warn{color:#e6b422}</style>
<h2>%s <span style="font-size:13px;color:#93a2b0">%s / %s / %s</span></h2>
<p><a href="../index.html">← 活动主页</a> ｜ <a href="../../../index.html">工作区主页</a></p>
<p class="warn">%s</p>
<h3>候选作业（%d 份，已落在 jobs/）</h3>
<table><tr><th>id</th><th>文件</th><th>结论</th><th>缺员</th><th>规模</th></tr>%s</table>
""" % (title, title, account, activity_name(activity), st["stage_id"], why, len(kept), rows))


def generate_workspace(account: str, activity: str, limit: int = 6,
                      include_derived: bool = False, fetch_levels: bool = True,
                      only_stages: list[str] | None = None, quiet: bool = False) -> dict:
    """**一次生成整个活动**：每关候选作业 + 编辑器页 + 活动主页 + 账号页。

    `only_stages`（stageId 或代号）用于单关重生成 —— 与"整活动"共用同一条代码路径，
    免得单关模式和生产模式长出两套行为。`cleared.json` / `notes.md` 永不被覆盖。
    """
    account = _safe_name(account)
    activity = _safe_name(activity)
    box, box_file = load_account_box(account)
    stages = activity_stages(activity)
    if not stages:
        raise SystemExit("活动 %s 在关卡表里没有可用关卡（试 `--list` 看有什么）" % activity)
    if len(stages) > MAX_AUTO_STAGES and not only_stages:
        # `--activity main` 会命中 268 关：每关都要拉关卡数据 + 画图，一次误操作几十分钟。
        # 真要整章生成就显式逐关 --stage，或者把这里的上限调大（上限不是"不支持"）。
        raise SystemExit("活动 %s 有 %d 关（超过一次生成上限 %d）。"
                         "要整块做请用 --stage 逐关指定；或调大 tools/workspace.py 的 MAX_AUTO_STAGES"
                         % (activity, len(stages), MAX_AUTO_STAGES))
    if only_stages:
        want = set(only_stages)
        stages = [s for s in stages if s["stage_id"] in want or s["code"] in want]
        if not stages:
            raise SystemExit("--stage 指定的关卡不在活动 %s 里：%s" % (activity, only_stages))

    (WS / account).mkdir(parents=True, exist_ok=True)
    shutil.copy2(box_file, WS / account / "box.json")     # 快照：结论只对这份 box 成立
    if not quiet:
        print("工作区 %s / %s（%s）｜ box %s（%d 人）｜ %d 关"
              % (account, activity, activity_name(activity), box_file.name,
                 len(box["owned_names"]), len(stages)))
    out = []
    for st in stages:
        out.append(generate_stage(account, activity, st, box, box_file, limit,
                                  include_derived, fetch_levels, quiet))
    write_activity_index(account, activity, out)
    write_account_index(account)
    write_root_index()
    n_ok = sum(1 for r in out if r["page_kind"] == "editor")
    n_clear = sum(1 for r in out if r["cleared"])
    if not quiet:
        print("活动主页 → %s" % (WS / account / activity / "index.html"))
        print("合计：%d 关（编辑器页 %d，兜底页 %d ｜ 已通关 %d）"
              % (len(out), n_ok, len(out) - n_ok, n_clear))
    return {"account": account, "activity": activity, "activity_name": activity_name(activity),
            "stages": out, "n_editor": n_ok, "n_cleared": n_clear,
            "index": str(WS / account / activity / "index.html")}


# ── 索引页（根 / 账号 / 活动）──

_CSS = """*{box-sizing:border-box}
body{background:#161a1e;color:#d8dee6;font:14px/1.6 system-ui,"Microsoft YaHei";margin:0;padding:20px 22px}
a{color:#8ac;text-decoration:none}a:hover{text-decoration:underline}
h2{margin:0 0 4px;font-size:18px}h3{margin:22px 0 6px;font-size:14px;color:#8ac}
.crumb{color:#93a2b0;font-size:12px;margin-bottom:14px}
table{border-collapse:collapse;width:100%;max-width:920px}
td,th{border-bottom:1px solid #242c33;padding:7px 10px;text-align:left;font-size:13px}
th{color:#8ac;font-weight:600}tr:hover{background:#1b2026}
.ok{color:#6bd47a}.no{color:#93a2b0}.warn{color:#e6b422}
.tag{display:inline-block;background:#242c34;border-radius:4px;padding:0 6px;margin-right:4px;font-size:12px;color:#cfd6dd}
.miss{color:#ff9b9b;font-size:12px}
.tip{color:#93a2b0;font-size:12px;margin-top:14px;max-width:920px}
"""


def _page(title: str, body: str, crumb: str = "") -> str:
    return ("<!doctype html><meta charset=\"utf-8\"><title>%s</title><style>%s</style>"
            "<h2>%s</h2>%s%s" % (title, _CSS, title,
                                 ("<div class=\"crumb\">%s</div>" % crumb) if crumb else "", body))


def write_activity_index(account: str, activity: str, summaries: list[dict] | None = None) -> pathlib.Path:
    """活动主页：关卡 × [N 份作业] × [已通关]。点任意一关进编辑器。"""
    account, activity = _safe_name(account), _safe_name(activity)
    base = WS / account / activity
    if summaries is None:                    # 从磁盘重建（保存 cleared 后也走这条）
        summaries = []
        for d in sorted(base.glob("*/")):
            sj, mj = d / "stage.json", d / "manifest.json"
            if not sj.is_file():
                continue
            st = json.loads(sj.read_text(encoding="utf-8"))
            man = json.loads(mj.read_text(encoding="utf-8")) if mj.is_file() else {}
            cand = (man.get("candidates") or [])
            import versions as VSN
            vs = VSN.tree_summary(d)
            summaries.append({"stage_id": st.get("stage_id"), "code": st.get("code") or d.name,
                              "name": st.get("name"), "group": st.get("group") or "main",
                              "n_jobs": len(list((d / "jobs").glob("*.json"))),
                              "n_versions": vs["n"],
                              "n_human_changes": sum(n.get("n_changes", 0) for n in vs["nodes"]
                                                     if n.get("author") == "human"),
                              "pool_total": len(cand), "cleared": (d / "cleared.json").is_file(),
                              "level_status": st.get("level_status") or man.get("level_status"),
                              "page_kind": "editor",
                              "best": ({"id": cand[0]["id"], "match": cand[0]["match"],
                                        "n_missing": cand[0]["n_missing"],
                                        "missing": cand[0]["missing"][:4]} if cand else None)})
        summaries.sort(key=lambda x: (GROUPS.get(x.get("group") or "main", 9),
                                      _natural(x.get("code") or "")))
    rows = []
    n_clear = n_ok = 0
    last_grp = None
    for s in summaries:
        cl = s.get("cleared")
        n_clear += 1 if cl else 0
        n_ok += 1 if s.get("page_kind") == "editor" else 0
        if s.get("group") != last_grp:              # 主线/EX/教学 分组标题（16 行平铺看不出分界）
            last_grp = s.get("group")
            rows.append('<tr><th colspan="6" style="color:#93a2b0;font-weight:600;padding-top:12px">%s</th></tr>'
                        % GROUP_LABEL.get(last_grp, last_grp or "其他"))
        best = s.get("best")
        if best and best.get("n_missing"):
            btxt = '<span class="miss">最好一份缺 %d（%s）</span>' % (
                best["n_missing"], "、".join(best.get("missing") or []) or "?")
        elif best:
            btxt = '<span class="ok">最好一份不缺人</span>'
        elif s.get("pool_total") == 0:
            btxt = '<span class="no">池子里没有这一关的作业</span>'
        else:
            btxt = ""
        extra = ("<span class=\"tag warn\">无地图：%s</span>" % (s.get("level_status") or "")
                 if s.get("page_kind") != "editor" else "")
        # 版本 = 修改树规模：`v3` 表示这关被人改过两轮；改了几处是迭代闭环的原料
        nv = s.get("n_versions") or 0
        vt = ('<span class="tag">%d 版</span>' % nv) if nv else '<span class="no">—</span>'
        if s.get("n_human_changes"):
            vt += '<span class="miss">改 %d 处</span>' % s["n_human_changes"]
        rows.append('<tr><td><a href="./%s/index.html">%s</a></td><td>%s</td>'
                    '<td>%s</td><td>%s</td><td class="%s">%s</td><td>%d/%d</td><td>%s</td></tr>'
                    % (_safe_name(s["code"]), s["code"], s.get("name") or "",
                       '<span class="tag">%d 份作业</span>' % s.get("n_jobs", 0), vt,
                       "ok" if cl else "no", "已通关 ✓" if cl else "未通关",
                       s.get("n_jobs", 0), s.get("pool_total", 0), btxt + extra))
    body = ("<table><tr><th>关卡</th><th>名称</th><th>候选</th><th>版本</th><th>状态</th>"
            "<th>选中/池子</th><th>匹配</th></tr>%s</table>"
            "<div class=\"tip\">「已通关」= 该关目录里有 <code>cleared.json</code>（人改完存下来的那份）。"
            "同关不同账号的 <code>cleared.json</code> 放一起就是差异数据："
            "<code>python tools/job_diff.py --stage &lt;关卡&gt;</code>。<br>"
            "没有地图数据的关卡进不去编辑器，只列候选（页面上会说明缺什么）。</div>" % "".join(rows))
    return _atomic_write(base / "index.html",
                         _page("%s · %s" % (activity_name(activity), account), body,
                               '<a href="../../index.html">← 工作区主页</a> ｜ %s / %s ｜ 编辑器页 %d/%d ｜ 已通关 %d/%d'
                               % (account, activity_name(activity), n_ok, len(summaries),
                                  n_clear, len(summaries))))


def list_accounts() -> list[str]:
    if not WS.is_dir():
        return []
    return sorted(d.name for d in WS.iterdir()
                  if d.is_dir() and ((d / "box.json").is_file() or (d / "index.html").is_file()))


def list_activities(account: str) -> list[str]:
    base = WS / _safe_name(account)
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if d.is_dir())


def write_account_index(account: str) -> pathlib.Path:
    """账号主页：这个号生成过哪些活动（关卡数 / 已通关数）。"""
    account = _safe_name(account)
    acts = list_activities(account)
    rows = []
    for a in acts:
        base = WS / account / a
        stages = [d for d in base.glob("*/") if (d / "stage.json").is_file()]
        cl = sum(1 for d in stages if (d / "cleared.json").is_file())
        lab = activity_name(a)
        rows.append('<tr><td><a href="./%s/index.html">%s</a></td><td>%s</td>'
                    '<td>%d</td><td class="%s">%d</td>'
                    '<td><a href="./%s/index.html">活动主页 →</a></td></tr>'
                    % (_safe_name(a), lab, a, len(stages), "ok" if cl == len(stages) and cl else "",
                       cl, _safe_name(a)))
    box = WS / account / "box.json"
    nbox = ""
    if box.is_file():
        try:
            d = json.loads(box.read_text(encoding="utf-8"))
            own = d.get("own") or []
            nbox = " ｜ box %d 人（%s）" % (sum(1 for o in own if o.get("own")), box.name)
        except Exception:
            nbox = ""
    body = ("<table><tr><th>活动</th><th>id</th><th>关卡</th><th>已通关</th><th></th></tr>%s</table>"
            "<div class=\"tip\">生成/重生成：<code>python tools/viz_plan.py --workspace %s "
            "--activity &lt;活动id&gt;</code><br>"
            "保存要用可写服务：<code>python tools/ws_server.py</code> → "
            "浏览器开 <code>http://127.0.0.1:8897/</code></div>" % ("".join(rows), account))
    return _atomic_write(WS / account / "index.html",
                         _page("账号 %s%s" % (account, nbox), body,
                               '<a href="../index.html">← 工作区主页</a>'))


def write_root_index() -> pathlib.Path:
    """工作区根主页：账号列表。"""
    accs = list_accounts()
    rows = []
    for a in accs:
        acts = list_activities(a)
        n_st = sum(1 for x in acts for d in (WS / a / x).glob("*/") if (d / "stage.json").is_file())
        n_cl = sum(1 for x in acts for d in (WS / a / x).glob("*/") if (d / "cleared.json").is_file())
        rows.append('<tr><td><a href="./%s/index.html">%s</a></td><td>%d</td><td>%d</td>'
                    '<td class="ok">%d</td></tr>' % (a, a, len(acts), n_st, n_cl))
    body = ("<table><tr><th>账号</th><th>活动</th><th>关卡</th><th>已通关</th></tr>%s</table>"
            "<div class=\"tip\">工作区按「账号 × 活动 × 关卡」组织：每关带候选作业、"
            "人改完的 <code>cleared.json</code> 与备注。<br>"
            "生成：<code>python tools/viz_plan.py --workspace &lt;账号&gt; --activity &lt;活动&gt;</code><br>"
            "可写服务（保存 cleared 用）：<code>python tools/ws_server.py</code></div>"
            % "".join(rows))
    return _atomic_write(WS / "index.html", _page("排轴工作区", body))


# ── 保存（编辑器 POST 落盘 / 命令行收编）──
#
# **每次保存都是一个版本节点**（`versions/vN_<author>.json` + meta）：迭代闭环要靠它
# 记下"这一步相对父版本改了哪几处"，所以保存不能只盖写 cleared.json —— 那样改动就没了。
# 父节点从提交上来的 `from_job`（编辑器里选中的那份的 src）推出来。

def resolve_parent(sd: pathlib.Path, src: str, explicit: str | None = None,
                   version_ids: set | None = None) -> tuple[str | None, str]:
    """提交上来的 src → (父版本 id | None, base_src)。

    - `versions/v2_human.json` → 父 = `v2_human`
    - `cleared.json` → 父 = `cleared_meta.version`（保存过关版时记下的那个节点）
    - `jobs/<候选>.json` → 若这份候选**正是根节点 v1_ai 的来源**，父 = `v1_ai`
      （用户改的就是 AI 给的那份，树该读成 `v1_ai → v2_human`，而不是并列的第二棵根）；
      否则无版本父，base_src 记下是改的哪份候选
    - 显式给了 `parent` 就用它（编辑器知道自己在改哪一份，这是权威）
    """
    import versions as VSN
    if explicit:
        return explicit, str(src or "")
    s = str(src or "").replace("\\", "/")
    m = re.search(r"(?:^|/)versions/(v\d+[a-z]*_\w+)\.json$", s)
    if m:
        return m.group(1), s
    if s.endswith("cleared.json"):
        cm = sd / "cleared_meta.json"
        if cm.is_file():
            try:
                v = (json.loads(cm.read_text(encoding="utf-8")) or {}).get("version")
            except Exception:
                v = None
            if v and any(x["id"] == v for x in VSN.load_tree(sd)):
                return v, s
        return None, s
    if "/jobs/" in s or s.startswith("jobs/"):
        base = s.rsplit("/", 1)[-1]
        for n in VSN.load_tree(sd):
            if not n.get("parent") and (n.get("base_src") or "").rsplit("/", 1)[-1] == base:
                return n["id"], s          # 改的就是根节点那份候选 → 挂到根下面
    return None, s


def _prepare_save(account: str, activity: str, key: str, job: dict):
    sd = find_stage_dir(account, activity, key)
    if not sd:
        raise SystemExit("工作区里没有这一关：%s / %s / %s" % (account, activity, key))
    st = json.loads((sd / "stage.json").read_text(encoding="utf-8"))
    if not isinstance(job, dict) or not job.get("actions"):
        raise SystemExit("作业没有 actions，拒绝保存（空作业会把 cleared.json 变成废文件）")
    return sd, st


def _save_common(account: str, activity: str, key: str, job: dict, mark_cleared: bool,
                 notes: str | None, from_job: str, note: str, author: str,
                 parent: str | None) -> dict:
    import versions as VSN
    sd, st = _prepare_save(account, activity, key, job)
    pid, base_src = resolve_parent(sd, from_job, parent)
    node = VSN.add_version(sd, job, author=author, parent=pid, base_src=base_src,
                          note=note, mark_cleared=mark_cleared, stage_id=st.get("stage_id") or "",
                          account=account, activity=activity, code=st.get("code") or "")
    if notes is not None:
        _atomic_write(sd / "notes.md", notes if notes.endswith("\n") else notes + "\n")
    write_activity_index(account, activity)
    r = {"ok": True, "stage_id": st.get("stage_id"), "code": st.get("code"),
         "version": node["id"], "author": author, "parent": pid or None,
         "n_changes": node["n_changes"], "changes": node["changes"],
         "versions": VSN.tree_summary(sd), "cleared": False}
    if mark_cleared:
        r["cleared"] = str((sd / "cleared.json").relative_to(PROJ)).replace("\\", "/")
    return r


def save_cleared(account: str, activity: str, key: str, job: dict,
                 notes: str | None = None, from_job: str = "", note: str = "",
                 author: str = "human", parent: str | None = None) -> dict:
    """保存为**通关版**：落一个版本节点 + 同步 `cleared.json`（两者是同一份内容）。

    `key` 可以是代号（SR-1）或 stageId（act54side_01）。stage_name 一律以 `stage.json` 为准 ——
    作业被存进错关卡会让下次跨账号比对整片错位（旧版 `D.stage` 未注入就出过这事）。
    """
    return _save_common(account, activity, key, job, True, notes, from_job, note, author, parent)


def save_version(account: str, activity: str, key: str, job: dict, note: str = "",
                 from_job: str = "", author: str = "human", parent: str | None = None,
                 notes: str | None = None) -> dict:
    """保存为**新版本**（不动 `cleared.json`）：试一版新轴、还没打过关时用这条。"""
    return _save_common(account, activity, key, job, False, notes, from_job, note, author, parent)


def save_notes(account: str, activity: str, key: str, notes: str) -> dict:
    sd = find_stage_dir(account, activity, key)
    if not sd:
        raise SystemExit("工作区里没有这一关：%s / %s / %s" % (account, activity, key))
    _atomic_write(sd / "notes.md", str(notes or ""))
    return {"ok": True, "notes": str((sd / "notes.md").relative_to(PROJ)).replace("\\", "/")}


def _latest_download(sd: pathlib.Path) -> pathlib.Path:
    sid = json.loads((sd / "stage.json").read_text(encoding="utf-8")).get("stage_id")
    dl = pathlib.Path.home() / "Downloads"
    cands = sorted(dl.glob("%s__*.json" % sid), key=lambda x: x.stat().st_mtime, reverse=True)
    if not cands:
        raise SystemExit("下载目录 %s 里没有 %s__*.json —— 先在页面上点「导出当前作业 JSON」"
                         % (dl, sid))
    return cands[0]


def ingest_cleared(account: str, activity: str, key: str, src: str | None = None,
                   note: str = "", parent: str | None = None) -> dict:
    """命令行收编：把页面上「导出当前作业 JSON」下载的那份收成**通关版**（含版本节点）。

    **为什么要有这条**：页面由静态 http.server 托管时没有写接口（POST 501），
    保存只能落到浏览器下载目录 —— 与既有的 `viz_plan --ingest` 是同一个道理：
    导出→导回是一条命令，不是零命令，这是设计前提。
    """
    sd = find_stage_dir(account, activity, key)
    if not sd:
        raise SystemExit("工作区里没有这一关：%s / %s / %s" % (account, activity, key))
    p = pathlib.Path(src) if src else _latest_download(sd)
    if not p.is_file():
        raise SystemExit("找不到文件：%s" % p)
    return save_cleared(account, activity, key,
                        json.loads(p.read_text(encoding="utf-8")), None,
                        pathlib.Path(p).name, note=note, parent=parent)


def ingest_version(account: str, activity: str, key: str, src: str | None = None,
                   note: str = "", parent: str | None = None) -> dict:
    """同 `ingest_cleared`，但只落版本节点（不动 `cleared.json`）。"""
    sd = find_stage_dir(account, activity, key)
    if not sd:
        raise SystemExit("工作区里没有这一关：%s / %s / %s" % (account, activity, key))
    p = pathlib.Path(src) if src else _latest_download(sd)
    if not p.is_file():
        raise SystemExit("找不到文件：%s" % p)
    return save_version(account, activity, key,
                        json.loads(p.read_text(encoding="utf-8")), note=note,
                        from_job=pathlib.Path(p).name, parent=parent)


# ── CLI ──

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="排轴工作区：一次生成整个活动")
    ap.add_argument("--account", help="账号 id（对应 data/operbox_batch/<账号>.json）")
    ap.add_argument("--activity", help="活动 id，如 act54side")
    ap.add_argument("--stage", action="append", default=None,
                    help="只重生成这一关（代号或 stageId，可重复）；不给=整个活动")
    ap.add_argument("--limit", type=int, default=6, help="每关挑进工作区的候选数（默认 6）")
    ap.add_argument("--include-derived", action="store_true", help="把自产件（既算过的轴）也算候选")
    ap.add_argument("--no-fetch", action="store_true", help="缺关卡数据也不联网拉")
    ap.add_argument("--list", action="store_true", help="列账号/活动后就退出")
    ap.add_argument("--save-cleared", nargs="+", metavar="ARG",
                    help="把导出的作业收编成 cleared.json："
                         "--save-cleared <账号> <活动> <关卡> [文件路径]（不给路径=取下载目录最新）")
    ap.add_argument("--save-version", nargs="+", metavar="ARG",
                    help="同上但**只落版本节点、不动 cleared**："
                         "--save-version <账号> <活动> <关卡> [文件路径]")
    ap.add_argument("--note", default="", help="配 --save-version/--save-cleared：这条改动的备注")
    ap.add_argument("--parent", default=None, help="配 --save-*：父版本 id（默认从文件路径推）")
    ap.add_argument("--rebuild-index", action="store_true",
                    help="让产物与磁盘一致：重写索引页 + 重画编辑器页（页面是烘死的快照）")
    ap.add_argument("--index-only", action="store_true",
                    help="配 --rebuild-index：只重写索引，不重画编辑器页（快）")
    a = ap.parse_args(argv)

    if a.list:
        print("工作区：%s" % WS)
        for ac in list_accounts():
            acts = ", ".join("%s(%s)" % (x, activity_name(x)) for x in list_activities(ac))
            print("  %-10s %s" % (ac, acts or "（还没生成过活动）"))
        print("\n作业池里最近的活动（可作为 --activity 的值）：")
        for x in recent_activities(12):
            n = len(activity_stages(x, include_nonbattle=True))
            print("  %-14s %s ｜ 关卡表里有 %d 关（含剧情/教学）" % (x, activity_name(x), n))
        return 0

    if a.save_cleared or a.save_version:
        args = a.save_cleared or a.save_version
        if len(args) < 3:
            raise SystemExit("--save-cleared/--save-version 需要：<账号> <活动> <关卡> [文件路径]")
        ac, act, key = args[:3]
        src = args[3] if len(args) > 3 else None
        fn = ingest_cleared if a.save_cleared else ingest_version
        r = fn(ac, act, key, src, note=a.note, parent=a.parent)
        n = len(json.loads((PROJ / (r.get("cleared") or
                                   "data/workspaces/%s/%s/%s/versions/%s.json"
                                   % (ac, act, key, r["version"]))).read_text(
            encoding="utf-8")).get("actions") or [])
        print("✓ %s：%s（%s，改 %d 处，%d 动作）"
              % ("收编为 cleared" if a.save_cleared else "落成新版本",
                 r.get("cleared") or r["version"], r.get("version"),
                 r.get("n_changes", 0), n))
        return 0

    if a.rebuild_index:
        # **重建 = 让产物与磁盘一致**，不只是重写两张索引 html：编辑器页是**烘死的快照**
        # （`ws.cleared`、下拉里有哪几份文件，都是生成那一刻的状态）。删过 cleared.json /
        # 版本文件之后不重画页面，页面就会一边显示"已通关 ✓"、一边指向一个不存在的文件（实测踩到）。
        n_pages = n_fail = 0
        for ac in list_accounts():
            boxf = WS / ac / "box.json"
            for act in list_activities(ac):
                for d in sorted((WS / ac / act).glob("*/")):
                    if not (d / "stage.json").is_file() or a.index_only:
                        continue
                    try:
                        st = json.loads((d / "stage.json").read_text(encoding="utf-8"))
                        _write_editor_page(ac, act, st, d, boxf)
                        n_pages += 1
                    except Exception as e:
                        n_fail += 1
                        print("   ! %s/%s/%s 重画失败：%s: %s" % (ac, act, d.name,
                                                               type(e).__name__, e))
                write_activity_index(ac, act)
            write_account_index(ac)
        write_root_index()
        print("✓ 索引页已重建：%s%s"
              % (WS / "index.html",
                 ("（编辑器页重画 %d，失败 %d）" % (n_pages, n_fail)) if not a.index_only
                 else "（--index-only：只重写索引，编辑器页没重画）"))
        return 0

    if not a.account or not a.activity:
        ap.print_help()
        return 2
    generate_workspace(a.account, a.activity, limit=a.limit,
                       include_derived=a.include_derived,
                       fetch_levels=not a.no_fetch, only_stages=a.stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
