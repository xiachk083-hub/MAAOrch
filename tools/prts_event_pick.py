#!/usr/bin/env python3
"""活动作业筛选（精英化感知）—— 一次把一个活动的「普通 + 突袭」全挑完。

为什么又要一个筛选器（和 prts_job_finder.py / pick_copilot.py 的分工）:
    · prts_job_finder.py —— 只看「有没有这个干员」，接入 Web 作业实验室，快但会误判：
      本号有一堆精0 Lv1 的干员，播下去开局就漏怪，它照样标「能跑」。
    · pick_copilot.py   —— 会核精英化/等级，但干员箱是写死的常量、只适合单关临时看。
    · 本工具            —— 精英化感知 + 一次跑完整个活动（含突袭），并可落盘给
      maa_job_fallback.py 直接降级重试（输出字段与它约定的一致：id/title/missing/requirements/rating/like）。

判定口径（全部对着干员箱的 elite/level/potential 核，不猜）:
    miss  作业点名要的干员本号没有（组内候选全没有也算 miss）
    low   有干员但精英化/等级/潜能够不上作业要求 —— 这是「险地开局暴毙」的主因，
          也是本工具相对 prts_job_finder 的关键差别；顺带汇总「差多少练度」供补练
    soft  作者要求专精/模组（作业站 JSON 里的 skill_level/module）——
          干员箱查不到专精模组，只能标注，交给 MAA 编队那一刻判（maa_job_fallback 会换下一份）
    weak  作业用到的干员本号是精0 / 精1 Lv40 以下（薄环节），数量越少越稳
    借人  miss 恰好为 1（作业站惯例：缺一个可以借好友助战顶上）
    broken 作者占位/半成品：actions 为空，MAA 根本跑不了（标题常写「这关MAA做不了」「半成品」）

站点字段坑（实测）:
    · 作业站要求里的潜能字段是 potentiality（不是 MAA 文档里的 potential）
    · 列表接口只回元数据，动作数/正文警告要另外 GET /copilot/get/{id}
    · 检索键是 stage_name（act54side_ex01），拿 SR-EX-1 去搜会有漏网

用法:
    python tools/prts_event_pick.py --event act54side --operbox data/operbox_pasted_0915b.json
    python tools/prts_event_pick.py --stages act54side_ex01,act54side_ex02 --operbox box.json --top 10
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
CACHE = PROJ / "data" / "copilot" / "_get_cache.json"
ELITE = {0: "精0", 1: "精1", 2: "精2"}

# 标题里出现这些 = 作者自己写明的坑，光核干员筛不出来
WARN_TITLE = ("手动", "无法自动", "MAA做不了", "看视频", "卡轴", "无法代理", "需要手操", "半成品")
# 正文里的坑要精确匹配：见过作者写「不要手动切换二倍速」，用 "手动" 裸词会误判成需手动
WARN_BODY = re.compile(r"(无法自动|不能自动|MAA做不了|需要手动|需手动|手动(?:放|选|开|点|部署)|看视频|半成品|卡轴|手操)")
WARN_SAFE = re.compile(r"(?:不要|无需|不用|禁止|别)(?:去)?(?:手动|手操)")
# 越省心的打法越靠前（挂机/无核/挂件兼容 = 不挑练度、不用手操）
STYLE_GOOD = ("挂机", "无核", "摆完", "挂件兼容", "简单好抄", "一键")


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


def load_box(path: Path) -> dict[str, dict]:
    """干员箱：list（含 own 字段）或 {"own": [...]} 两种格式都吃"""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else (data.get("own") or [])
    return {o["name"]: o for o in rows if o.get("own")}


def parse_stages(event: str = "", stages: str = "") -> list[str]:
    """act54side → act54side_01..08 + tr01 + ex01..08（活动固定骨架）"""
    if stages:
        return [s.strip() for s in stages.split(",") if s.strip()]
    if not event:
        raise SystemExit("需要 --event 或 --stages")
    out = [f"{event}_{i:02d}" for i in range(1, 9)] + [f"{event}_tr01"]
    out += [f"{event}_ex{i:02d}" for i in range(1, 9)]
    return out


def display_name(stage: str) -> str:
    """act54side_ex03 → SR-EX-3（前缀取固定的关卡字母，如 act54side=SR）"""
    m = re.match(r"act54side(?:_(ex|tr)?(\d+))?$", stage)
    if m:
        kind, num = m.group(1), m.group(2)
        if not num:
            return "SR"
        return f"SR-{kind.upper()}-{int(num)}" if kind else f"SR-{int(num)}"
    return stage


def query_stage(stage: str, limit: int = 60) -> list[dict]:
    """按 stage_name + 显示名各查一次（有的作者标题写 SR-EX-3、stage 挂错，两条都收，之后严格过滤）"""
    items: dict[int, dict] = {}
    for kw in (stage, display_name(stage)):
        try:
            d = get(f"{API}/copilot/query?limit={limit}&level_keyword={urllib.parse.quote(kw)}")
            for it in d["data"]["data"]:
                items.setdefault(it["id"], it)
        except Exception as e:
            print(f"  ! 查询失败 {kw}: {e}")
        time.sleep(0.25)
    return list(items.values())


def inline_content(it: dict) -> dict | None:
    c = it.get("content")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except Exception:
            return None
    return c or None


def full_content(jid: int) -> dict | None:
    """作业正文（带 actions）—— 列表接口只回元数据，动作数要单独拉；同一份作业缓存复用"""
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    url = f"{API}/copilot/get/{jid}"
    if url not in cache:
        try:
            cache[url] = get(url)
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return None
        time.sleep(0.25)
    c = (cache[url].get("data") or {}).get("content")
    if isinstance(c, str):
        c = json.loads(c)
    return c or None


def judge_one(nm: str, req: dict, box: dict) -> tuple[str, str, int, dict | None]:
    """单个干员 → (级别, 说明, 薄弱计数, 练度缺口)；级别 ∈ ok / miss / low"""
    me = box.get(nm)
    if not me:
        return "miss", f"缺{nm}", 0, None
    hav_e, hav_l = me.get("elite", 0), me.get("level", 0)
    tag = f"{nm}{ELITE.get(hav_e, '精?')}{hav_l}"
    weak = 1 if (hav_e == 0 or hav_l < 40) else 0
    need_e = req.get("elite") or 0
    need_l = req.get("level") or 1
    need_p = req.get("potential") or req.get("potentiality") or 0
    short = []
    if hav_e < need_e:
        short.append(f"需{ELITE[need_e]}")
    elif hav_e == need_e and hav_l < need_l:
        short.append(f"需Lv{need_l}")
    if need_p and me.get("potential", 0) < need_p:
        short.append(f"需{need_p}潜")
    if short:
        gap = {"name": nm, "have": f"{ELITE.get(hav_e, '精?')}Lv{hav_l}", "need": "/".join(short)}
        return "low", f"{tag}<{'/'.join(short)}", weak, gap
    extra = ""
    if req.get("skill_level"):
        extra += f" 要专{req['skill_level']}"
    if req.get("module"):
        extra += f" 要模{req['module']}"
    return "ok", f"{tag}{extra}", weak, None


def evaluate(content: dict, box: dict) -> dict:
    """按精英化/等级/潜能核一遍，返回判定明细"""
    miss: list[str] = []
    low: list[str] = []
    soft: list[str] = []
    notes: list[str] = []
    gaps: list[dict] = []
    weak = 0
    elite2: list[str] = []
    used: list[str] = []

    def take(nm: str, req: dict, prefix: str = "") -> None:
        nonlocal weak
        lvl, note, w, gap = judge_one(nm, req, box)
        notes.append(prefix + note)
        weak += w
        if lvl == "miss":
            miss.append(nm)
        elif lvl == "low":
            low.append(nm)
            if gap and gap not in gaps:
                gaps.append(gap)
        else:
            used.append(nm)
            if box.get(nm, {}).get("elite", 0) >= 2:
                elite2.append(nm)
            if req.get("skill_level") or req.get("module"):
                soft.append(note.strip())

    for o in content.get("opers") or []:
        take(o.get("name", ""), o.get("requirements") or {})
    for g in content.get("groups") or []:
        cands = g.get("opers") or []
        pick = None
        for c in cands:                      # 组内优先挑「有且够练度」的
            nm, req = c.get("name", ""), c.get("requirements") or {}
            if nm in box and judge_one(nm, req, box)[0] == "ok":
                pick = (nm, req)
                break
        if pick is None:                     # 没满足的，退而求其次：有干员但练度不够 → low
            for c in cands:
                nm, req = c.get("name", ""), c.get("requirements") or {}
                if nm in box:
                    pick = (nm, req)
                    break
        if pick is None:
            miss.append(f"[{g.get('name') or '组'}]")
            notes.append(f"[{g.get('name') or '组'}]候选{len(cands)}个全没有")
        else:
            take(pick[0], pick[1], prefix=f"[{g.get('name') or '组'}]→")

    doc = content.get("doc") or {}
    title = (doc.get("title") or "").strip()
    details = WARN_SAFE.sub("", doc.get("details") or "")   # 先剔掉「不要手动…」，再找坑
    warn = [w for w in WARN_TITLE if w in title] + (WARN_BODY.findall(details[:600]) or [])
    warn = list(dict.fromkeys(warn))
    style = sum(1 for w in STYLE_GOOD if w in title)
    raid = any(k in title for k in ("突袭", "险地"))
    normal = ("普通" in title) or (not raid and content.get("difficulty") in (0, 1))
    return {"miss": miss, "low": low, "soft": soft, "notes": notes, "gaps": gaps, "weak": weak,
            "elite2": elite2, "used": used, "title": title, "warn": warn, "style": style,
            "raid": raid, "normal": normal}


def rank_key(r: dict) -> tuple:
    """排序：能跑 → 不借人 → 非废作业 → 不挑练度(soft少) → 薄环节少 → 打法省心 → 普通优先 → 赞多"""
    return (len(r["miss"]), len(r["low"]), 1 if len(r["miss"]) == 1 else 0,
            1 if r.get("broken") else 0, len(r["soft"]), r["weak"], -r.get("style", 0),
            0 if r["normal"] else 1, -r["like"])


def screen_stage(stage: str, box: dict, deep: int, limit: int = 60) -> list[dict]:
    rows = []
    for it in query_stage(stage, limit=limit):
        c = inline_content(it)
        if not c or c.get("stage_name") != stage:
            continue
        ev = evaluate(c, box)
        rows.append({"id": it["id"], "rating": it.get("rating_level") or 0, "like": it.get("like") or 0,
                     "views": it.get("views") or 0, "diff": c.get("difficulty"),
                     "stage_name": stage, "actions": None, "broken": False, **ev})
    rows.sort(key=rank_key)
    for r in rows[:deep]:                       # 只给最有希望的几份拉正文（动作数/正文警告）
        c = full_content(r["id"])
        if not c:
            continue
        acts = c.get("actions") or []
        r["actions"] = len(acts)
        r["broken"] = len(acts) == 0
        doc = c.get("doc") or {}
        for w in ([x for x in WARN_TITLE if x in (doc.get("title") or "")] +
                  WARN_BODY.findall(WARN_SAFE.sub("", doc.get("details") or "")[:600])):
            if w not in r["warn"]:
                r["warn"].append(w)
    rows.sort(key=rank_key)
    return rows


def label(r: dict) -> str:
    if r["broken"]:
        return "废作业"
    if not r["miss"] and not r["low"]:
        return "★能跑"
    if len(r["miss"]) == 1 and not r["low"]:
        return "可借人"
    if r["miss"]:
        return "缺" + ",".join(r["miss"][:2])
    return "差练度" + ",".join(r["low"][:2])


def fmt(r: dict) -> str:
    lb = label(r)
    mark = {"★能跑": "✓", "可借人": "~", "废作业": "×"}.get(lb, "✗")
    mode = "突袭" if r["raid"] else ("普通" if r["normal"] else "未标")
    warn = (" ⚠" + "".join(r["warn"][:2])) if r["warn"] else ""
    return (f"    {mark} id={r['id']:<7} ★{r['rating']:<2} 赞{r['like']:<5} {mode:<4} {lb:<16} "
            f"动作{r['actions'] if r['actions'] is not None else '?':<4} {r['title'][:32]}{warn}")


def grade(p: dict) -> str:
    """首选的可执行性一句话结论 —— 直接决定用户要不要在 MAA 里开助战、要不要先补练度"""
    warn = "作者注明需手动" if p["warn"] else ""
    tail = []
    if p["miss"]:
        tail.append("缺" + ",".join(p["miss"][:2]) + ("（借）" if len(p["miss"]) == 1 else ""))
    if p["low"]:
        tail.append("差练度" + ",".join(p["low"][:2]))
    if not warn and not tail:
        return "直接能跑"
    if not tail:
        return warn
    return (warn + "+" if warn else "") + ("+".join(tail))


def pick(rows: list[dict], raid: bool) -> dict | None:
    """该模式下的首选：能跑 → 可借人 → 至少作者没自曝要手动；占位废作业永不入选"""
    pool = [r for r in rows if r["raid"] == raid and not r["broken"]]
    if not pool:
        return None
    clean = [r for r in pool if not r["miss"] and not r["low"] and not r["warn"]]
    borrow = [r for r in pool if len(r["miss"]) == 1 and not r["low"] and not r["warn"]]
    return (clean or borrow or [r for r in pool if not r["warn"]] or pool)[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="", help="活动前缀，如 act54side → 自动展开 8 关 + 教学 + EX1-8")
    ap.add_argument("--stages", default="", help="逗号分隔 stage_name（与 --event 二选一）")
    ap.add_argument("--operbox", required=True, type=Path)
    ap.add_argument("--show", type=int, default=8, help="每关打印几行")
    ap.add_argument("--deep", type=int, default=14, help="每关给前几份拉正文核动作数")
    ap.add_argument("--out", default="", type=Path, help="筛选报告 json（默认 data/copilot/<event>_screening.json）")
    ap.add_argument("--dump-dir", default="", help="把每关首选作业正文落盘到该目录（MAA 本地加载用）")
    args = ap.parse_args()

    stages = parse_stages(args.event, args.stages)
    box = load_box(args.operbox)
    ev = args.event or re.match(r"[a-z0-9]+", stages[0]).group(0)
    out = args.out or (PROJ / "data" / "copilot" / f"{ev}_screening.json")

    print(f"干员箱 {args.operbox.name}（拥有 {len(box)} 名）｜活动 {ev}｜{len(stages)} 关\n")
    report, malist, gap_stat, md = {}, [], {}, [f"# {ev} 作业筛选（按本号练度）\n",
                                                f"干员箱：`{args.operbox.name}`（{len(box)} 名）\n"]
    dumped: set[str] = set()
    for st in stages:
        rows = screen_stage(st, box, args.deep)
        if not rows:
            print(f"== {display_name(st)} ({st}) ==  作业站上没有（未解锁/冷门/编号不对）\n")
            md.append(f"## {display_name(st)}（{st}）\n\n作业站上没有可用作业。\n")
            continue
        clean = [r for r in rows if not r["miss"] and not r["low"] and not r["broken"] and not r["warn"]]
        borrow = [r for r in rows if len(r["miss"]) == 1 and not r["low"] and not r["broken"] and not r["warn"]]
        print(f"== {display_name(st)} ({st}) ==  作业{len(rows)}份｜直接能跑 {len(clean)}｜需借1人 {len(borrow)}")
        for r in rows[:args.show]:
            print(fmt(r))
        for r in rows[:max(args.show, 4)]:
            if r["gaps"]:
                g = r["gaps"][0]
                gap_stat[(g["name"], g["have"], g["need"])] = gap_stat.get((g["name"], g["have"], g["need"]), 0) + 1
        picks = {}
        md.append(f"## {display_name(st)}（{st}）\n")
        for rd in (False, True):
            p = pick(rows, rd)
            if not p:
                continue
            mode = "突袭" if rd else "普通"
            picks[mode] = p
            g = grade(p)
            print(f"    → {mode}首选 prts://{p['id']}（{p['title'][:30]}）[{g}]"
                  f"  {'／'.join(p['notes'][:4])[:110]}")
            md.append(f"- **{mode}首选**：`prts://{p['id']}` {p['title']} — {g}"
                      f"（★{p['rating']} 赞{p['like']} 动作{p['actions']}"
                      + (f" 薄弱环节{p['weak']}" if p["weak"] else "") + "）")
            if p["notes"]:
                md.append(f"  - 编队核对：{'／'.join(p['notes'])}")
            if p["notes"] and (p["miss"] or p["low"]):
                md.append(f"  - 待解决：{'；'.join(p['miss'] + p['low'])}")
        # 备选：同关「点赞最高的可跑那份」——首选多为零要求作业，备选常是要专三的热门作业，
        # 两者 MAA 里会依次试（maa_job_fallback 按报告顺序降级），列出来省得用户再翻作业站
        used_ids = {p["id"] for p in picks.values()}
        alts = sorted([r for r in rows if r["id"] not in used_ids and not r["broken"] and not r["warn"]
                       and not r["low"] and len(r["miss"]) <= 1], key=lambda r: -r["like"])
        if alts:
            a = alts[0]
            print(f"      备选（点赞最高）prts://{a['id']} 赞{a['like']}（{a['title'][:26]}）[{grade(a)}]")
            md.append(f"- 备选（点赞最高）：`prts://{a['id']}` {a['title']} — {grade(a)}"
                      f"（★{a['rating']} 赞{a['like']} 动作{a['actions']}）")
        print()
        report[st] = rows
        if args.dump_dir:
            d = Path(args.dump_dir)
            d.mkdir(parents=True, exist_ok=True)
            for mode, p in picks.items():
                c = full_content(p["id"])
                if not c:
                    continue
                f = d / f"{st}_{p['id']}.json"
                f.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
                dumped.add(f.name)
                malist.append({"Name": st, "IsNavNameOverride": False, "FilePath": str(f),
                               "CopilotId": p["id"], "IsRaid": mode == "突袭",
                               "IsChecked": True, "Index": len(malist)})

    if args.dump_dir:                            # 清掉上一轮（改了排序/换了干员箱）残留的作业文件，
        for f in Path(args.dump_dir).glob("*.json"):   # 免得 MAA 里加载到旧的那份
            if f.name not in dumped:
                f.unlink()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"报告：{out}")
    if malist:
        mf = out.with_name(out.stem + "_malist.json")
        mf.write_text(json.dumps(malist, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"MAA 多作业列表：{mf}")
    if gap_stat:
        print("\n补练度清单（按「补上去能解锁的作业份数」排，前 12 条）：")
        md.append("\n## 补练度清单\n")
        for (nm, hav, need), n in sorted(gap_stat.items(), key=lambda x: -x[1])[:12]:
            print(f"    {nm}: 现在 {hav} → 作业要 {need}（涉及 {n} 份）")
            md.append(f"- {nm}：现在 {hav} → 作业要 {need}（影响 {n} 份作业）")
    md_path = out.with_suffix(".md")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"人读版清单：{md_path}")


if __name__ == "__main__":
    main()
