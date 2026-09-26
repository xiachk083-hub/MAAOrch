#!/usr/bin/env python3
"""prts 作业筛选器 —— 按账号干员范围（含精英化/等级），从作业站挑出「能直接跑」的作业。

由来（2026-09-15 实测）:
    作业站（prts.maa.plus / prts.plus）的检索键是 **stage_name**（如 act54side_ex01），
    用游戏里显示的关卡名（SR-EX-1）去查会一条都搜不到。作业的干员要求分两处：
      · opers[]   —— 点名要的干员（各自可能带 requirements 精英化/等级/专精/模组/潜能）
      · groups[]  —— 候选组（组里任意一个达标即可，如「高台核→维什戴尔」）
    只看 opers 会把组里的可用干员漏掉，误判成「缺人」。

    光判「有没有这个干员」不够：热门作业普遍要求精二 Lv60~90，而干员箱里有玛恩纳
    精二 Lv1、精一 Lv70 这类「解锁了没练」的干员，光看拥有人人会以为能跑。所以这里
    把 requirements 拆成三档：
      · 精英化/等级/潜能 —— 干员箱查得到，硬判（缺了就是跑不了）
      · 专精/模组        —— MAA OperBox 不导出，只能当风险提示列出来
      · 候选组           —— 组内优先挑「练度达标」的那一个，都差就挑缺口最小的那个

产出:
    <out>.json        每关排序后的候选（missing / under 练度缺口 / requirements / 评分）
    <out>_malist.json MAA「多作业模式」可直接用的 TaskList 片段（FilePath 指向本地作业文件）
    <out>.md          人类可读的推荐表（首选 + 备选 + 该练谁）

用法:
    python tools/prts_job_finder.py --stages act54side_ex01,act54side_ex02 --operbox box.json
    python tools/prts_job_finder.py --display SR-EX-1..SR-EX-8 --operbox box.json --dump-dir data/copilot/event54
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
API = "https://prts.maa.plus"
UA = {"User-Agent": "Mozilla/5.0"}

# 作业标题里常见的昵称 → 正式名（保守：只收明确的别名，不同干员绝不合并）
ALIASES = {
    "歌蒂": "歌蕾蒂娅", "歌帝": "歌蕾蒂娅",
    "图亚": "娜仁图亚",
    "EW": "维什戴尔", "ew": "维什戴尔", "维神": "维什戴尔",
    "42": "史尔特尔", "42t": "史尔特尔",
    "叔叔": "玛恩纳",
    "白咕咕": "白面鸮",
    "小羊": "艾雅法拉",
    "狗妹": "纯烬艾雅法拉",
    "塞妈": "塞雷娅",
    "桃子": "桃金娘",
    "夜刀": "麒麟R夜刀",
    "肥鸭": "菲亚梅塔",
    "苇草": "焰影苇草",
    "百嘉": "百炼嘉维尔",
}

_CN = {0: "零", 1: "一", 2: "二", 3: "三"}


def norm(name) -> str:
    """干员/组名归一。**None 安全**（2026-09-24 修）。

    作业里有些 action **没有 name**（如 `{"type":"SpeedUp"}`，或显式 `"name": null`）⇒
    调用点写的 `a.get("name","")` 会返回 **None**（`dict.get` 的默认值只在 key 缺失时生效），
    `name.strip()` 立刻 `AttributeError: 'NoneType' object has no attribute 'strip'`；
    而 CLI 只在最后才写产物 ⇒ **一崩整批全丢**（planner-1 拉 H8-1 那批就是这么丢的）。
    这里把 None/非字符串统一兜住，8 个调用点全都自动安全。
    """
    s = str(name or "").strip()
    return ALIASES.get(s, s)


def get(url: str, retry: int = 3) -> dict:
    last = None
    for _ in range(retry):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return json.load(r)
        except Exception as e:      # 网络抖动重试，别让一条失败拖垮整轮
            last = e
            time.sleep(0.6)
    raise last


_KNOWN: set[str] = set()          # 干员全表（含未拥有），load_owned 时填充


def load_owned(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else (data.get("own") or data.get("own_opers") or [])
    # 干员全表（含未拥有）——MAA 的 OperBox 导出会把没抽到的也列出来，
    # 拿来分辨 actions 里点名的到底是干员还是召唤物/装置（见 is_operator）
    global _KNOWN
    _KNOWN = _KNOWN | {o["name"] for o in rows if o.get("name")}
    return {o["name"]: o for o in rows if o.get("own")}


def is_operator(name: str, owned: dict, reqs_acts: list[dict] | None = None) -> bool:
    """actions 里点名的这个"东西"是干员吗？

    作业会把召唤物/装置也写成 Deploy —— 望的「棋子」、本期的「战术锚点」「风雪之眼」，
    这类名字在干员表里查不到。把它们当成缺干员会把好作业误杀（102695 一度被 棋子 顶掉）。
    判据分层：
      · MAA 的 OperBox 导出是**全干员表**（含没抽到的，实测 429 条），有它就以表为准；
      · 只有残缺名单时退化成看朝向：direction 全是 None 的多半是召唤物/装置。
    """
    if name in owned:
        return True
    if name in _KNOWN:
        return True
    if len(_KNOWN) >= 300:               # 全表在手 → 表里没有就不是干员
        return False
    acts = reqs_acts or []
    return any(a.get("direction") not in (None, "None") for a in acts)


def stage_names(display: str) -> list[str]:
    """SR-EX-1..SR-EX-8 / SR-1..SR-8 → act54side_ex01.. / act54side_01..（活动固定前缀）"""
    m = re.match(r"^(SR)-?(EX)?-?(\d+)\.\.(?:SR-?)" + ("(EX)?" if "-EX-" in display else "") + r"-?(\d+)$", display, re.I)
    if not m:
        return [display]
    ex, a, b = bool(m.group(2)), int(m.group(3)), int(m.group(5))
    pre = "act54side_ex" if ex else "act54side_"
    fmt = (lambda i: f"{i:02d}") if ex else (lambda i: f"{i:02d}")
    return [f"{pre}{fmt(i)}" for i in range(a, b + 1)]


def check_req(oper: dict, req: dict) -> list[str]:
    """拿作业里的 requirements 核对干员箱 → 返回练度缺口（空列表 = 达标）。

    能硬判的: elite / level / potentiality（干员箱里有真值）
    判不了的: skill_level（技能专精）、module（模组）—— MAA OperBox 不导出这两项，
             只能作为风险提示，不在缺口里算。
    """
    gaps: list[str] = []
    e, lv = oper.get("elite") or 0, oper.get("level") or 0
    want_e, want_lv = req.get("elite"), req.get("level")
    if isinstance(want_e, int) and want_e >= 0:
        if e < want_e:
            gaps.append(f"精{_CN.get(want_e, want_e)}")                 # 精英化都没到
        elif e == want_e and want_lv and want_lv > lv:
            gaps.append(f"精{_CN.get(e, e)}Lv{want_lv}")                # 精英化够了等级不够
    elif want_lv and want_lv > lv:
        gaps.append(f"Lv{want_lv}")
    want_p = req.get("potentiality")
    if isinstance(want_p, int) and want_p > (oper.get("potential") or 0):
        gaps.append(f"潜{want_p}")
    return gaps


def gap_text(name: str, oper: dict, gaps: list[str]) -> str:
    e = oper.get("elite") or 0
    return f"{name} 需 {'/'.join(gaps)}（你是精{_CN.get(e, e)}Lv{oper.get('level') or 0}）"


def _listed(content: dict) -> tuple[set, set]:
    """作业点过名的干员：opers + 各组候选；组名单独返回（actions 会用组名代指整组）"""
    names, gnames = set(), set()
    for o in content.get("opers") or []:
        names.add(norm(o.get("name", "")))
    for g in content.get("groups") or []:
        gnames.add(norm(g.get("name") or ""))
        for o in g.get("opers") or []:
            names.add(norm(o.get("name", "")))
    return names, gnames


def evaluate(content: dict, owned: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    """返回 (缺干员, 硬要求(专精/模组，查不到只能提示), 命中的自有干员, 练度缺口)

    干员来源有三处，漏掉哪一处都会把「缺人/练度不够」误判成「零缺口」：
      · opers —— 正常写法，自带 requirements
      · groups —— 每组出一个，组内候选任意一个自有且达标即可
      · actions 里点名的 —— 有的作业把 opers 留空只靠 actions 驱动（TR-1 那几份就是），
        这些干员一样要上场、一样会因为没有精英化而打不过，必须一起核
    """
    miss: list[str] = []
    reqs: list[str] = []
    used: list[str] = []
    under: list[str] = []
    seen: set[str] = set()

    def take(name: str, req: dict) -> bool:
        if name in seen:
            return True
        seen.add(name)
        oper = owned.get(name)
        if oper is None:
            miss.append(name)
            return False
        used.append(name)
        if req.get("skill_level") or req.get("module"):
            reqs.append(f"{name} 专{req.get('skill_level', '-')} 模{req.get('module', '-')}")
        gaps = check_req(oper, req)
        if gaps:
            under.append(gap_text(name, oper, gaps))
            return False
        return True

    for o in content.get("opers", []):
        take(norm(o.get("name", "")), o.get("requirements") or {})
    for g in content.get("groups", []):
        opts = [(norm(x.get("name", "")), x.get("requirements") or {}) for x in g.get("opers", [])]
        mine = [c for c in opts if c[0] in owned]
        ok = next((c for c in mine if not check_req(owned[c[0]], c[1])), None)
        if ok:
            take(*ok)                       # 组里挑一个练度达标的自有干员
        elif mine:
            take(*min(mine, key=lambda c: len(check_req(owned[c[0]], c[1]))))   # 都差点 → 记最接近的
        else:
            miss.append(f"[{g.get('name')}]")
    listed, gnames = _listed(content)
    acts: dict[str, list[dict]] = {}
    for a in content.get("actions") or []:
        nm = norm(a.get("name", ""))
        if nm:
            acts.setdefault(nm, []).append(a)
    for nm, group in acts.items():
        if nm in listed or nm in gnames or nm in seen:
            continue
        if not is_operator(nm, owned, group):
            continue                        # 召唤物/装置：不需要拥有
        take(nm, {})                        # opers 没列、只靠 actions 点名的上场干员
    return miss, reqs, used, under


def detail(content: dict, owned: dict) -> list[dict]:
    """逐干员核对表：这份作业要谁上场、作业写的要求、你号上的精英化/等级。

    MAA 编队时先按 requirements 卡人，卡不过就换下一个候选；但 requirements 是可选字段，
    作者不写不等于不用练度 —— 所以没写要求的干员也要把「你=精XLY」摊开看，
    精零/一级的干员放进突袭关就是白给。
    """
    out: list[dict] = []

    def add(name: str, req: dict, via: str) -> None:
        oper = owned.get(name)
        row = {"name": name, "req": req, "via": via, "owned": oper is not None,
               "elite": (oper or {}).get("elite"), "level": (oper or {}).get("level"),
               "rarity": (oper or {}).get("rarity") or 0,
               "gaps": check_req(oper, req) if oper else []}
        # 没写要求的干员：精零就上场的，多半是没练过的挂件，单独标出来
        row["risk"] = bool(oper) and not req and row["rarity"] >= 4 and (row["elite"] or 0) == 0
        out.append(row)

    for o in content.get("opers") or []:
        add(norm(o.get("name", "")), o.get("requirements") or {}, "opers")
    for g in content.get("groups") or []:
        opts = [(norm(x.get("name", "")), x.get("requirements") or {}) for x in g.get("opers", [])]
        mine = [c for c in opts if c[0] in owned]
        pick = next((c for c in mine if not check_req(owned[c[0]], c[1])), None) or \
               (min(mine, key=lambda c: len(check_req(owned[c[0]], c[1]))) if mine else None)
        if pick:
            add(pick[0], pick[1], f"组[{g.get('name')}]")
    listed, gnames = _listed(content)
    acts: dict[str, list[dict]] = {}
    for a in content.get("actions") or []:
        nm = norm(a.get("name", ""))
        if nm:
            acts.setdefault(nm, []).append(a)
    for nm, group in acts.items():
        if nm in listed or nm in gnames or any(r["name"] == nm for r in out):
            continue
        if is_operator(nm, owned, group):
            add(nm, {}, "actions")
        else:
            out.append({"name": nm, "req": {}, "via": "召唤物", "owned": False,
                        "elite": None, "level": None, "rarity": 0, "gaps": [], "risk": False})
    return out


_GET_CACHE = PROJ / "data" / "copilot" / "_get_cache.json"


def cached_get(url: str) -> dict:
    """作业正文缓存：同一份作业反复筛（换账号筛）时不必重复拉取，省几分钟"""
    cache = {}
    if _GET_CACHE.exists():
        try:
            cache = json.loads(_GET_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if url in cache:
        return cache[url]
    data = get(url)
    cache[url] = data
    _GET_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _GET_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return data


def query_stage(stage: str) -> list[tuple[dict, dict]]:
    """一次查询就带回该关全部候选（content 里已有 opers/groups，够判练度）——不必逐份 get"""
    out, page = [], 1
    while page <= 10:
        d = get(f"{API}/copilot/query?limit=200&page={page}"
                f"&level_keyword={urllib.parse.quote(stage)}").get("data") or {}
        for it in d.get("data") or []:
            c = it.get("content")
            if isinstance(c, str):
                try:
                    c = json.loads(c)
                except Exception:
                    continue
            if isinstance(c, dict) and c.get("stage_name") == stage:
                out.append((it, c))
        if not d.get("has_next"):
            break
        page += 1
    return out


def _rank_key(r: dict) -> tuple:
    """排序：能不能跑 > 作者有没有打警示 > 社区验证度。

    为什么人气要压过「要求条数」：作业里 requirements 是可选的，作者不写 ≠ 练度不用够；
    实测「零要求」那一批多是 ★0 赞0 的自用/半成品上传（见过标题直接写「半成品前20杀」），
    而 ★10 赞几百份是被人跑通过的。所以同档里先看点赞/浏览，再把查不到的专精/模组
    条数当扣分项（每条都是跑失败的风险面）。
    """
    trust = -(min(r["like"], 300) + 20 * r["rating"] + min(r["views"], 20000) / 100) + 8 * len(r["requirements"])
    return (len(r["missing"]) + len(r["under"]), len(r["missing"]), len(r["warn"]), trust)


def screen(stage: str, owned: dict, top: int = 8, full: int = 4) -> list[dict]:
    rows = []
    for it, c in query_stage(stage):
        # 必须逐份拉正文再判：列表接口把 actions 剥掉了，不拉就既认不出「只挂视频的空作业」，
        # 也核不到「opers 留空、只靠 actions 点名」的上场干员（TR-1 那几份就是）
        body, fetched = c, False
        try:
            got = cached_get(f"{API}/copilot/get/{it['id']}").get("data", {}).get("content")
            if isinstance(got, str):
                got = json.loads(got)
            if isinstance(got, dict) and got:
                body, fetched = got, True
        except Exception:
            pass
        miss, reqs, used, under = evaluate(body, owned)
        title = ((body.get("doc") or c.get("doc") or {}).get("title") or "")[:40]
        # 标题里自带"要手动/没法自动/半成品"这类字样的，说明作者自己都不推荐直接代理，
        # 排到最后（见过作者直接在标题里写「这关MAA做不了，看视频自己手动放吧」，光看干员是筛不出来的）
        warn = [w for w in ("手动", "无法自动", "MAA做不了", "看视频", "卡轴", "无法代理",
                            "半成品", "未测试", "自用", "已失效", "废弃") if w in title]
        acts = body.get("actions") or []
        if fetched and not acts:
            # actions 为空 = MAA 无从执行：opers/groups 也空的是纯视频/占位作业；
            # 有干员没动作的至少还能编队，但照样跑不出结果
            warn = sorted(set(warn + ["空作业" if not (body.get("opers") or body.get("groups"))
                                      else "无操作"]))
        rows.append({"id": it["id"], "rating": it.get("rating_level") or 0, "like": it.get("like") or 0,
                     "views": it.get("views") or 0, "title": title, "stage_name": stage,
                     "missing": miss, "under": under, "requirements": reqs, "used": used, "warn": warn,
                     "actions": len(acts), "can_run": (not miss) and (not under),
                     "clean": (not miss) and (not under) and (not reqs),
                     # 作业难度（prts 语义）：0=当前难度 / 1=普通 / 2=突袭 / 3=普通+突袭通用。
                     # 2026-09-22 补：没有它就没法给「突袭」挑作业 —— 挑到 difficulty=1 的会在突袭页面上跑普通轴。
                     "difficulty": body.get("difficulty") if isinstance(body.get("difficulty"), int) else None})
    # 排序：能不能跑 > 作者有没有打警示 > 社区验证度（见 _rank_key）
    rows.sort(key=_rank_key)
    return rows


def detail_of(job_id: int, owned: dict) -> list[dict]:
    """给某份作业出逐干员核对表（含精英化/等级），用于报告里摊开给人看"""
    body = cached_get(f"{API}/copilot/get/{job_id}").get("data", {}).get("content")
    if isinstance(body, str):
        body = json.loads(body)
    return detail(body or {}, owned) if isinstance(body, dict) else []


def _flag(r: dict) -> str:
    if "空作业" in r["warn"]:
        return "空作业(仅视频)"
    if "无操作" in r["warn"]:
        return "无操作(跑不了)"
    if r["clean"]:
        return "★零缺口"
    if r["can_run"]:
        return "可跑(专模待验)"
    if r["missing"]:
        return "缺" + ",".join(r["missing"][:2])
    return "练度缺" + str(len(r["under"]))


def _line(row: dict) -> str:
    """把一名干员的核对结果写成人话：你=精2Lv70 ✓(需精2Lv60)"""
    nm = row["name"]
    if row["via"] == "召唤物":
        return f"{nm}（召唤物/装置，不需要拥有）"
    if not row["owned"]:
        return f"缺 **{nm}**"
    you = f"你=精{row['elite']}Lv{row['level']}"
    r = row["req"]
    if r.get("elite") is not None or r.get("level") is not None:
        want = f"需精{r.get('elite', '-')}Lv{r.get('level', '-')}"
        return f"{nm} {you} {'✓' if not row['gaps'] else '✗'}（{want}）"
    if row["risk"]:
        return f"{nm} {you} ⚠️（作业没写要求，但这是精零上场）"
    return f"{nm} {you}（作业没写练度要求，只能靠这个判断够不够）"


def _write_md(path: Path, report: dict, picks: dict, operbox: str, owned: dict) -> None:
    need: dict[str, int] = {}
    lines = [f"# 活动作业推荐（按干员箱筛选）", "",
             f"- 干员箱：`{operbox}`", f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}",
             f"- 判定口径：干员箱里查得到的**精英化/等级/潜能**硬判；专精与模组查不到，只作风险提示",
             f"- 每关首选作业下面附**逐干员核对表**（含精英化），作业没写要求的干员也一并摊开", ""]
    for st, rows in report.items():
        p = picks.get(st)
        lines.append(f"## {st}")
        if not p:
            lines.append("（无候选）")
            lines.append("")
            continue
        lines.append(f"**首选** `prts://{p['id']}` ★{p['rating']} 赞{p['like']} — {p['title']}")
        if p["missing"]:
            lines.append(f"- ⚠️ 缺干员：{'、'.join(p['missing'])}（可借战补，最多借 1）")
        if p["under"]:
            lines.append(f"- ⚠️ 练度不够：{'；'.join(p['under'])}")
            for u in p["under"]:
                nm = u.split(" ")[0]
                need[nm] = need.get(nm, 0) + 1
        if p["requirements"]:
            lines.append(f"- 查不到的要求（专精/模组，跑失败多半卡这）：{'；'.join(p['requirements'])}")
        if p["warn"]:
            lines.append(f"- ⚠️ 标题警告：{'、'.join(p['warn'])}")
        det = detail_of(p["id"], owned)
        if det:
            lines.append("- 逐干员核对：")
            for row in det:
                lines.append(f"    - {_line(row)}")
        alts = [r for r in rows if r["id"] != p["id"]][:3]
        if alts:
            lines.append("- 备选：" + "｜".join(
                f"`prts://{a['id']}` {a['title'][:22]}（{_flag(a)}）" for a in alts))
        lines.append("")
    if need:
        lines += ["## 该练谁（按卡关份数排）", ""]
        for nm, n in sorted(need.items(), key=lambda kv: -kv[1])[:12]:
            lines.append(f"- **{nm}** — 卡住 {n} 关的首选作业")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="", help="逗号分隔的 stage_name")
    ap.add_argument("--display", default="", help="显示名区间，如 SR-EX-1..SR-EX-8")
    ap.add_argument("--operbox", required=True, type=Path, help="干员 JSON（MAA OperBox 输出或上传的名单）")
    ap.add_argument("--out", default="data/copilot/screening.json", type=Path)
    ap.add_argument("--dump-dir", default="", help="把首选作业正文落盘到该目录（供 MAA 本地加载）")
    ap.add_argument("--top", type=int, default=8, help="每关在结果 JSON 里保留的候选数（全量都会评估）")
    ap.add_argument("--full", type=int, default=4, help="每关补拉正文（取动作数）的候选数")
    args = ap.parse_args()

    stages: list[str] = []
    if args.stages:
        stages += [s.strip() for s in args.stages.split(",") if s.strip()]
    if args.display:
        stages += stage_names(args.display)
    if not stages:
        raise SystemExit("需要 --stages 或 --display")

    owned = load_owned(args.operbox)
    e2 = [o for o in owned.values() if (o.get("elite") or 0) >= 2]
    print(f"干员箱 {args.operbox.name}：拥有 {len(owned)} 名（精二 {len(e2)}）｜评估 {len(stages)} 关\n")

    report, malist, picks = {}, [], {}
    for st in stages:
        rows = screen(st, owned, top=args.top, full=args.full)
        clean = [r for r in rows if r["clean"]]
        runnable = [r for r in rows if r["can_run"]]
        # 首选 = 排序第一名，但跳过 MAA 根本执行不了的（只有视频的空作业 / 有干员没动作）
        dead = ("空作业", "无操作")
        usable = [r for r in rows if not any(w in r["warn"] for w in dead)] or rows
        pick = usable[0] if usable else None
        picks[st] = pick
        print(f"== {st} ==  候选{len(rows)} ｜零缺口 {len(clean)} ｜干员齐且练度够 {len(runnable)}")
        for r in rows[:4]:
            req = ("  查不到: " + "; ".join(r["requirements"][:2])) if r["requirements"] else ""
            print(f"    id={r['id']:<7} ★{r['rating']} 赞{r['like']:<5} 动{r['actions']:<3} {_flag(r):<12} "
                  f"{r['title'][:26]}{req}")
        report[st] = rows[:args.top]
        if pick and args.dump_dir:
            d = Path(args.dump_dir)
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{st}_{pick['id']}.json"
            body = cached_get(f"{API}/copilot/get/{pick['id']}").get("data", {}).get("content")
            f.write_text(json.dumps(body if not isinstance(body, str) else json.loads(body),
                                    ensure_ascii=False, indent=1), encoding="utf-8")
            malist.append({"Name": st, "IsNavNameOverride": False, "FilePath": str(f),
                           "CopilotId": pick["id"], "IsRaid": False, "IsChecked": True,
                           "Index": len(malist)})
        if pick:
            note = ""
            if pick["missing"]:
                note += f" 缺 {'、'.join(pick['missing'])}"
            if pick["under"]:
                note += f" 练度缺 {'；'.join(pick['under'])}"
            print(f"    → 首选 prts://{pick['id']} （{pick['title']}）{note}")
            for row in detail_of(pick["id"], owned):
                print(f"        {_line(row)}")
            print()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写出筛选结果：{args.out}")
    md = args.out.with_suffix(".md")
    _write_md(md, report, picks, args.operbox.name, owned)
    print(f"推荐表：{md}")
    if malist:
        mf = args.out.with_name(args.out.stem + "_malist.json")
        mf.write_text(json.dumps(malist, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"MAA 列表片段：{mf}")


if __name__ == "__main__":
    main()
