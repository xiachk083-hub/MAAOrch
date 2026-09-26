#!/usr/bin/env python3
"""活动 act54side（SR-1..SR-8 / SR-TR-1 / SR-EX-1..SR-EX-8）作业挑选 — 一次性脚本。

和 tools/prts_job_finder.py 的区别（为什么另起一份）：
  1. 那份在会话期间被并行改动，我不动它；
  2. 它对 actions 里点名的干员核得太宽 ——「棋子」「风雪之眼」这类召唤物/棋子、
     以及教学关（TR）里游戏白给的干员，都被算成「缺干员」，把本该首选的作业挤下去。
     这里用「干员箱全表」过筛：只有真干员才参与核对，教学关的未拥有干员不计缺。
  3. 判据不变：精英化/等级/潜能硬判（干员箱里有真值），专精/模组查不到只作提示。

产出：data/copilot/event54_user_pick.json / .md
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
API = "https://prts.maa.plus"
UA = {"User-Agent": "Mozilla/5.0"}
BOX = PROJ / "data" / "operbox_pasted_0915d.json"
CACHE = PROJ / "data" / "copilot" / "_get_cache.json"
OUT = PROJ / "data" / "copilot" / "event54_user_pick"

CN = {0: "零", 1: "一", 2: "二", 3: "三"}
ALIASES = {"夜刀": "麒麟R夜刀", "EW": "维什戴尔", "维神": "维什戴尔", "叔叔": "玛恩纳",
           "小羊": "艾雅法拉", "白咕咕": "白面鸮", "塞妈": "塞雷娅", "桃子": "桃金娘"}
WARNS = ("手动", "无法自动", "MAA做不了", "看视频", "卡轴", "无法代理",
         "半成品", "未测试", "自用", "已失效", "废弃")
DIFF = {None: "普通关", 0: "未标注", 1: "仅普通", 2: "仅突袭", 3: "普通/突袭通用"}
STAGES = ([f"act54side_{i:02d}" for i in range(1, 9)] + ["act54side_tr01"]
          + [f"act54side_ex{i:02d}" for i in range(1, 9)])


def norm(n: str) -> str:
    return ALIASES.get(n.strip(), n.strip())


def get(url: str, retry: int = 3) -> dict:
    last = None
    for _ in range(retry):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return json.load(r)
        except Exception as e:
            last = e
            time.sleep(0.6)
    raise last


def body_of(jid: int) -> dict:
    """作业正文：先用站点的 get 缓存（之前几轮已经拉过），缺了再联网"""
    url = f"{API}/copilot/get/{jid}"
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    payload = cache.get(url)
    if payload is None:
        payload = get(url)
        cache[url] = payload
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    b = (payload.get("data") or {}).get("content")
    if isinstance(b, str):
        b = json.loads(b)
    return b or {}


def gaps_of(oper: dict, req: dict) -> list[str]:
    """精英化/等级/潜能硬判（专精与模组干员箱里没有，判不了）"""
    if not oper:
        return ["未拥有"]
    out: list[str] = []
    e, lv = oper.get("elite") or 0, oper.get("level") or 0
    we, wl = req.get("elite"), req.get("level")
    if isinstance(we, int) and we >= 0:
        if e < we:
            out.append(f"精{CN.get(we, we)}")
        elif e == we and wl and wl > lv:
            out.append(f"精{CN.get(e, e)}Lv{wl}")
    elif wl and wl > lv:
        out.append(f"Lv{wl}")
    wp = req.get("potentiality")
    if isinstance(wp, int) and wp > (oper.get("potential") or 0):
        out.append(f"潜{wp}")
    return out


def verdict(body: dict, owned: dict, roster: set, training: bool) -> dict:
    miss: list[str] = []
    under: list[str] = []
    reqs: list[str] = []
    seen: set[str] = set()

    def take(name: str, req: dict) -> None:
        if name in seen:
            return
        seen.add(name)
        oper = owned.get(name)
        if req.get("skill_level") or req.get("module"):
            reqs.append(f"{name} 专{req.get('skill_level', '-')} 模{req.get('module', '-')}")
        if not oper:
            miss.append(name)
            return
        g = gaps_of(oper, req)
        if g:
            under.append(f"{name} 需{'/'.join(g)}（你是精{CN.get(oper.get('elite') or 0)}"
                         f"Lv{oper.get('level') or 0}）")

    for o in body.get("opers") or []:
        take(norm(o.get("name", "")), o.get("requirements") or {})
    listed = {norm(o.get("name", "")) for o in body.get("opers") or []}
    for g in body.get("groups") or []:
        listed.add(norm(g.get("name") or ""))
        opts = [(norm(x.get("name", "")), x.get("requirements") or {}) for x in g.get("opers") or []]
        listed |= {c[0] for c in opts}
        mine = [c for c in opts if c[0] in owned]
        ok = next((c for c in mine if not gaps_of(owned[c[0]], c[1])), None)
        if ok:
            take(*ok)
        elif mine:
            take(*min(mine, key=lambda c: len(gaps_of(owned[c[0]], c[1]))))
        else:
            miss.append(f"[{g.get('name')}]")
    acts = body.get("actions") or []
    for a in acts:                                  # actions 里点名的上场干员
        nm = norm(a.get("name", ""))
        if not nm or nm in listed or nm not in roster:
            continue                                # 非干员（棋子/召唤物/组名）不参与核对
        if training and nm not in owned:
            continue                                # 教学关的干员是游戏发的
        take(nm, {})
    title = ((body.get("doc") or {}).get("title") or "")[:40]
    warn = [w for w in WARNS if w in title]
    if not acts:
        warn = sorted(set(warn + ["空作业"]))
    return {"missing": miss, "under": under, "requirements": reqs,
            "warn": warn, "actions": len(acts), "difficulty": body.get("difficulty")}


def flag_of(r: dict) -> str:
    if "空作业" in (r.get("warn") or []):
        return "空作业(仅视频)"
    if r.get("clean"):
        return "零缺口"
    if r.get("missing"):
        return "缺" + ",".join(r["missing"][:3])
    if r.get("under"):
        return f"练度缺{len(r['under'])}"
    return "可跑" if r.get("can_run") else "未核"


def rank(r: dict) -> tuple:
    """能不能跑 > 作者警示 > 社区验证度（人气压过「要求条数」：
    不写 requirements ≠ 不用练度，实测零要求那批多是 ★0 赞0 的自用/半成品）"""
    trust = -(min(r["like"], 300) + 20 * r["rating"] + min(r["views"], 20000) / 100) \
            + 8 * len(r.get("requirements") or [])
    return (len(r.get("missing") or []) + len(r.get("under") or []),
            len(r.get("missing") or []), len(r.get("warn") or []), trust)


def candidates(stage: str) -> list[dict]:
    rows, page = [], 1
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
                title = ((c.get("doc") or {}).get("title") or "")[:40]
                rows.append({"id": it["id"], "rating": it.get("rating_level") or 0,
                             "like": it.get("like") or 0, "views": it.get("views") or 0,
                             "title": title, "stage_name": stage})
        if not d.get("has_next"):
            break
        page += 1
    return rows


def main() -> None:
    box = json.loads(BOX.read_text(encoding="utf-8"))
    owned = {o["name"]: o for o in box if o.get("own")}
    roster = {o["name"] for o in box}
    print(f"干员箱 {BOX.name}：拥有 {len(owned)} 名（精二 "
          f"{sum(1 for o in owned.values() if (o.get('elite') or 0) >= 2)}）\n")

    report: dict[str, list[dict]] = {}
    for st in STAGES:
        rows = candidates(st)
        for r in rows:                              # 全量核：正文基本都在缓存里，缺的才联网
            try:
                r.update(verdict(body_of(r["id"]), owned, roster, "_tr" in st))
                r["verified"] = True
            except Exception:
                r["verified"] = False
            r["can_run"] = bool(r.get("verified")) and not r.get("missing") and not r.get("under")
            r["clean"] = r["can_run"] and not r.get("requirements")
        rows.sort(key=rank)
        report[st] = rows
        pick = rows[0]
        print(f"== {st} ==  候选 {len(rows)}")
        for r in rows[:4]:
            flag = flag_of(r)
            print(f"    prts://{r['id']:<7} {DIFF.get(r.get('difficulty'), ''):<9} 动{r.get('actions', 0):<3} "
                  f"★{r['rating']:<2} 赞{r['like']:<6} [{flag:<14}] {r['title'][:26]}")
        print(f"    → 首选 prts://{pick['id']} {DIFF.get(pick['difficulty'], '')}\n")

    OUT.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 活动 act54side 作业推荐（按你的干员箱筛）", "",
             f"- 干员箱：`{BOX.name}`（拥有 {len(owned)} 名 / 精二 "
             f"{sum(1 for o in owned.values() if (o.get('elite') or 0) >= 2)}）",
             f"- 生成：{time.strftime('%Y-%m-%d %H:%M')}",
             "- 口径：**精英化/等级/潜能硬判**（干员箱有真值）；专精/模组查不到，只作风险提示", ""]
    for st, rows in report.items():
        p = rows[0]
        lines.append(f"## {st}  ·  {DIFF.get(p['difficulty'], '')}")
        lines.append(f"**首选** `prts://{p['id']}` ★{p['rating']} 赞{p['like']} 动作{p['actions']} — {p['title']}")
        if p["missing"]:
            lines.append(f"- ⚠️ 缺干员：{'、'.join(p['missing'])}")
        if p["under"]:
            lines.append(f"- ⚠️ 练度不够：{'；'.join(p['under'])}")
        if p["requirements"]:
            lines.append(f"- 查不到的要求（专精/模组）：{'；'.join(p['requirements'])}")
        if p["warn"]:
            lines.append(f"- ⚠️ 标题警示：{'、'.join(p['warn'])}")
        lines.append("- 备选：" + "｜".join(
            f"`prts://{a['id']}` {a['title'][:20]}（{DIFF.get(a.get('difficulty'), '')}"
            f"，{flag_of(a)}）" for a in rows[1:4]))
        lines.append("")
    OUT.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{OUT.with_suffix('.json')} 与 {OUT.with_suffix('.md')}")


if __name__ == "__main__":
    main()
