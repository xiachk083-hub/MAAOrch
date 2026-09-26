#!/usr/bin/env python3
"""按「本号干员/练度」筛选作业站（ZOOT/prts）作业。

为什么需要它:
    作业站作业是按作者的号写的。同一个关卡上，有的作业开局用德克萨斯（本号精1 Lv70），
    有的用凛御银灰（本号精2 Lv90）—— 练度差一个档次，险地难度下就是「开局漏怪直接暴毙」。
    所以选作业不能只看标题，必须逐份把 required oper + 精英化/等级/技能等级 对照干员箱核一遍。

关卡编号换算（本仓库实测）:
    游戏里显示的 15-13 = copilot 里的 stage_name「main_15-12」，即 main_15-(N-1)。

用法:
    python tools/pick_copilot.py --display 15-14            # 只看险地
    python tools/pick_copilot.py --stage main_15-13 --hard
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

OPERBOX = PROJ / "data" / "operbox_emu0914_2301.json"
BATTLE_DATA = Path(r"D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64-1\resource\battle_data.json")
CACHE = PROJ / "data" / "copilot" / "_zoot_cache.json"
UA = {"User-Agent": "Mozilla/5.0"}
ELITE = {0: "精0", 1: "精1", 2: "精2"}


def api(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def load_own() -> dict:
    return {o["name"]: o for o in json.load(open(OPERBOX, encoding="utf-8"))["own"]}


def profession_map() -> dict:
    chars = json.load(open(BATTLE_DATA, encoding="utf-8"))["chars"]
    return {k: v.get("profession") for k, v in chars.items()}


def fetch_jobs(keywords: list[str]) -> dict:
    cache = json.load(open(CACHE, encoding="utf-8")) if CACHE.exists() else {}
    ids: dict[int, dict] = {}
    for kw in keywords:
        try:
            d = api(f"https://prts.maa.plus/copilot/query?limit=60&level_keyword={urllib.parse.quote(kw)}")
            for it in d["data"]["data"]:
                ids.setdefault(it["id"], it)
        except Exception as e:
            print(f"  ! 查询失败 {kw}: {e}", file=sys.stderr)
        time.sleep(0.3)
    for i in list(ids):
        if str(i) in cache:
            continue
        try:
            cache[str(i)] = api(f"https://prts.maa.plus/copilot/get/{i}")
            time.sleep(0.35)
        except Exception as e:
            print(f"  ! 拉取失败 {i}: {e}", file=sys.stderr)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return cache


def evaluate(content: dict, own: dict) -> tuple[bool, list[str], list[str], int]:
    """返回 (是否完全满足, 逐条说明, 本号精2干员名单, 薄弱环节数)

    薄弱环节 = 作业必需、但本号只有精0 / 精1 低等级的干员。
    险地下这类干员常是「开局漏怪直接暴毙」的起点（实测：德克萨斯精1 Lv70 打 15-13 险地）。
    """
    ok, notes, elite_ops, weak = True, [], [], 0

    def judge(nm: str, rq: dict) -> None:
        nonlocal ok, weak
        me = own.get(nm)
        if not me:
            ok = False
            notes.append(f"缺{nm}")
            return
        tag = f"{nm}{ELITE[me['elite']]}{me['level']}"
        if me["elite"] >= 2:
            elite_ops.append(nm)
        elif me["elite"] == 0 or me["level"] < 40:
            weak += 1
            tag += "(薄弱)"
        need_e, need_l = rq.get("elite", 0), rq.get("level", 1)
        if me["elite"] < need_e or (me["elite"] == need_e and me["level"] < need_l):
            ok = False
            notes.append(f"{tag}<需{ELITE[need_e]}{need_l}")
        else:
            extra = ""
            if rq.get("skill_level"):
                extra += f"专{rq['skill_level']}"
            if rq.get("module"):
                extra += f"模组{rq['module']}"
            notes.append(f"{tag}{extra}")

    for o in content.get("opers", []):
        judge(o.get("name", ""), o.get("requirements") or {})
    for g in content.get("groups", []):
        cands = [o.get("name") for o in g.get("opers", [])]
        hit = next((c for c in cands if c in own), None)
        if hit is None:
            ok = False
            notes.append(f"[{g.get('name')}]无可选({len(cands)}候选)")
        else:
            judge(hit, {})
            notes[-1] = f"[{g.get('name')}]→{notes[-1]}"
    return ok, notes, elite_ops, weak


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--display", help="游戏里显示的关卡号，如 15-14")
    ap.add_argument("--stage", help="stage_name，如 main_15-13")
    ap.add_argument("--hard", action="store_true", help="只看险地/突袭")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--dump", help="把排名第一的作业写到该路径")
    args = ap.parse_args()

    stage = args.stage
    if not stage and args.display:
        n = int(args.display.split("-")[1])
        stage = f"main_{args.display.split('-')[0]}-{n - 1}"
    if not stage:
        sys.exit("需要 --display 或 --stage")

    own = load_own()
    print(f"目标 stage_name={stage}（游戏里显示 {args.display or '?'}）｜号上拥有 {len(own)} 名干员")

    cache = fetch_jobs([args.display or stage, stage, "险地", "突袭"])
    rows = []
    for sid, wrap in cache.items():
        c = (wrap.get("data") or {}).get("content")
        if isinstance(c, str):
            c = json.loads(c)
        if not c or c.get("stage_name") != stage:
            continue
        title = ((c.get("doc") or {}).get("title") or "").strip()
        if args.hard and "险地" not in title and "突袭" not in title:
            continue
        ok, notes, elite_ops, weak = evaluate(c, own)
        rows.append({
            "id": sid, "title": title, "ok": ok, "notes": notes,
            "elite": elite_ops, "weak": weak, "actions": len(c.get("actions") or []),
            "sandbox": "沙盘" in title, "raid": ("险地" in title or "突袭" in title),
        })
    rows.sort(key=lambda r: (0 if r["ok"] else 1, r["weak"], 0 if r["sandbox"] else 1,
                             -len(r["elite"]), r["actions"]))
    print(f"\n匹配 {len(rows)} 份，按「完全可用 → 无薄弱环节 → 不依赖沙盘 → 本号精2干员多 → 动作少」排序：")
    for r in rows[:args.top]:
        flag = "✓" if r["ok"] else "✗"
        warn = f'薄弱x{r["weak"]}' if r["weak"] else "无薄弱"
        print(f'  {flag} id={r["id"]:<7} 动作{ r["actions"]:<3} {warn:<7} '
              f'{"险地" if r["raid"] else "普通"}{"+沙盘" if r["sandbox"] else "    "} {r["title"][:40]}')
        print(f'      精2 x{len(r["elite"])}: {", ".join(r["elite"][:8]) or "-"}')
        print(f'      {" | ".join(r["notes"])[:180]}')
    if args.dump and rows and rows[0]["ok"]:
        sid = rows[0]["id"]
        c = (cache[str(sid)].get("data") or {}).get("content")
        if isinstance(c, str):
            c = json.loads(c)
        Path(args.dump).parent.mkdir(parents=True, exist_ok=True)
        json.dump(c, open(args.dump, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f'\n已写出首选手作业：{args.dump}（id={sid}）')


if __name__ == "__main__":
    main()
