#!/usr/bin/env python3
"""吾导先路（act16side / GA-1..8 + GA-TR-1 + GA-EX-1..8）作业筛选 — 一次性脚本。

为什么不直接跑 prts_event_pick.py --event act16side:
  1. 那份的 display_name() 只认 act54side(SR)，GA 的显示名要另加映射
     （作业站标题写 GA-EX-3，检索键是 act16side_ex03）；
  2. 它的 screen_stage 要求 content.stage_name 严格等于 stage —— 作业站把
     「仅突袭」作业挂在 `act16side_ex03#f#` 上，会整批漏掉；吾导先路的 EX 关
     正是靠这批作业打突袭；
  3. 它的 rank_key 把「查不到的要求（专精/模组）」排在质量之前 → 首选会落到
     ★0 赞1 的 2 动作「自用」占位作业上。本脚本改成：**缺口优先（缺人手/缺练度/作者注明
     需手动/空作业），同档内按口碑与完整度排**，专精/模组只作风险提示。

产出: data/copilot/ga16_user_pick.json / .md（+ --dump-dir 落每关首选作业正文）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prts_event_pick import (API, ELITE, evaluate, full_content, get,  # noqa: E402
                             inline_content, load_box)

EVENT = "act16side"
LETTER = "GA"
WARN_TITLE = ("手动", "无法自动", "MAA做不了", "看视频", "卡轴", "无法代理",
              "半成品", "未测试", "已失效", "废弃")
FLAKY_TITLE = ("自用", "增补", "修改自用")
STAGE_NAMES = ([f"{EVENT}_{i:02d}" for i in range(1, 9)] + [f"{EVENT}_tr01"]
               + [f"{EVENT}_ex{i:02d}" for i in range(1, 9)])


def disp(stage: str) -> str:
    m = re.match(rf"{EVENT}_(?:(tr|ex)(\d+)|(\d+))$", stage)
    if not m:
        return stage
    kind, num, plain = m.groups()
    if plain:
        return f"{LETTER}-{int(plain)}"
    return f"{LETTER}-{kind.upper()}-{int(num)}"


def query(stage: str, limit: int = 100) -> list[dict]:
    """按 stage_name 子串查（'act16side_ex03' 会把 '#f#' 的仅突袭作业一起带出来）"""
    out: dict[int, dict] = {}
    for kw in (stage, f"{stage}#f#"):
        try:
            d = get(f"{API}/copilot/query?limit={limit}&level_keyword={urllib.parse.quote(kw)}")
            for it in d["data"]["data"]:
                out.setdefault(it["id"], it)
        except Exception as e:
            print(f"  ! 查询失败 {kw}: {e}")
        time.sleep(0.25)
    return list(out.values())


def screen(stage: str, box: dict) -> list[dict]:
    rows: dict[int, dict] = {}
    for it in query(stage):
        c = inline_content(it)
        if not c:
            continue
        sn = c.get("stage_name") or ""
        if sn not in (stage, f"{stage}#f#"):
            continue
        r = {"id": it["id"], "rating": it.get("rating_level") or 0,
             "like": it.get("like") or 0, "views": it.get("views") or 0,
             "diff": c.get("difficulty"), "stage_name": sn,
             "actions": None, "broken": False}
        r.update(evaluate(c, box))
        # 模式可打性：仅突袭作业挂 #f#；difficulty 2=仅突袭 / 1=仅普通 / 3=通用
        r["raid_only"] = sn.endswith("#f#") or c.get("difficulty") == 2
        r["normal_only"] = c.get("difficulty") == 1 and not sn.endswith("#f#")
        if r["raid_only"]:
            r["raid"] = True
        r["flaky"] = any(w in (r["title"] or "") for w in FLAKY_TITLE)
        rows[it["id"]] = r
    return list(rows.values())


def deepen(rows: list[dict], n: int) -> None:
    """给最有希望的 n 份拉正文（动作数/正文警告），原地更新"""
    for r in sorted(rows, key=rank)[:n]:
        c = full_content(r["id"])
        if not c:
            continue
        r["actions"] = len(c.get("actions") or [])
        r["broken"] = r["actions"] == 0
        doc = c.get("doc") or {}
        for w in WARN_TITLE:
            if w in (doc.get("title") or "") and w not in r["warn"]:
                r["warn"].append(w)


def rank(r: dict) -> tuple:
    """缺口优先 → 口碑/完整度 → 薄弱环节少。专精/模组（soft）不进排序，只作提示。"""
    clean = 0 if (not r["miss"] and not r["low"] and not r["warn"] and not r["broken"]
                  and (r["actions"] or 0) >= 3) else 1
    borrow = 0 if (len(r["miss"]) == 1 and not r["low"] and not r["warn"]) else 1
    return (clean, borrow, len(r["miss"]), len(r["low"]), len(r["warn"]),
            1 if r["broken"] else 0, 1 if r["flaky"] else 0,
            -r["like"], -r["rating"], r["weak"])


def capable(r: dict, raid: bool) -> bool:
    if raid:
        return not r["normal_only"]
    return not r["raid_only"]


def label(r: dict) -> str:
    if r["broken"]:
        return "空作业"
    if not r["miss"] and not r["low"] and not r["warn"]:
        return "✓能跑"
    if len(r["miss"]) == 1 and not r["low"]:
        return f"借{r['miss'][0]}"
    if r["miss"]:
        return "缺" + ",".join(r["miss"][:3])
    if r["low"]:
        return "差练度" + ",".join(r["low"][:2])
    return "作者注明" + ",".join(r["warn"][:2])


def concl(r: dict) -> str:
    """一句话结论：这份作业拿这个号能不能直接跑"""
    if r["broken"]:
        return "空作业（作者只放了视频）"
    tail = []
    if r["miss"]:
        tail.append("缺" + ",".join(r["miss"][:3]) + ("（可借1人）" if len(r["miss"]) == 1 else "（多于1人，借不了）"))
    if r["low"]:
        tail.append("差练度" + ",".join(r["low"][:3]))
    if r["warn"]:
        tail.append("作者注明" + ",".join(r["warn"][:2]))
    if r["soft"]:
        tail.append("未核专精/模组" + ",".join(dict.fromkeys(r["soft"]))[:80])
    return "直接能跑" if not tail else "；".join(tail)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--operbox", required=True, type=Path)
    ap.add_argument("--deep", type=int, default=20, help="每关给前几份拉正文")
    ap.add_argument("--out", default=PROJ / "data" / "copilot" / "ga16_user_pick.json", type=Path)
    ap.add_argument("--dump-dir", default="", help="把每关首选作业正文落盘（MAA 本地加载用）")
    args = ap.parse_args()

    box = load_box(args.operbox)
    e2 = sum(1 for o in box.values() if o.get("elite", 0) >= 2)
    print(f"干员箱 {args.operbox.name}（拥有 {len(box)} 名 / 精二 {e2}）"
          f"｜吾导先路 {EVENT}｜{len(STAGE_NAMES)} 关\n")

    report: dict = {}
    md = [f"# 吾导先路 {LETTER} 系列 作业推荐（按你的干员箱筛）\n",
          f"- 干员箱：`{args.operbox.name}`（拥有 {len(box)} 名 / 精二 {e2}）",
          f"- 生成：{time.strftime('%Y-%m-%d %H:%M')}",
          "- 口径：精英化/等级/潜能 **硬判**（干员箱有真值）；专精/模组查不到，只作风险提示",
          "- 覆盖：GA-1~8 + GA-TR-1 + GA-EX-1~8（含作业站 `#f#` 仅突袭作业）",
          "- 排序：先看缺口（缺人/缺练度/作者注明需手动/空作业），同档按口碑与完整度\n"]
    summary: list[tuple[str, str, str, str]] = []
    miss_tally: dict[str, int] = {}
    gap_tally: dict[tuple, int] = {}
    malist: list[dict] = []
    dumped: set[str] = set()

    for st in STAGE_NAMES:
        rows = screen(st, box)
        if not rows:
            print(f"== {disp(st)} ({st}) ==  作业站上没有\n")
            md.append(f"## {disp(st)}（{st}）\n\n作业站上没有可用作业（未开放/冷门/编号不对）。\n")
            summary.append((disp(st), "—", "—", "作业站无作业"))
            continue
        deepen(rows, args.deep)
        rows.sort(key=rank)
        for r in rows[:8]:
            for nm in r["miss"]:
                miss_tally[nm] = miss_tally.get(nm, 0) + 1
            for g in r["gaps"]:
                gap_tally[(g["name"], g["have"], g["need"])] = gap_tally.get((g["name"], g["have"], g["need"]), 0) + 1

        # 只有 GA-EX-* 有普通/突袭两套；GA-1~8 与教学关是纯普通关，不分模式
        modes = (False, True) if re.match(rf"{EVENT}_ex\d+$", st) else (False,)
        print(f"== {disp(st)} ({st}) ==")
        md.append(f"## {disp(st)}（{st}）\n")
        picks = {}
        for raid in modes:
            mode = "突袭" if raid else "普通"
            pool = sorted([r for r in rows if capable(r, raid)], key=rank)
            if not pool:
                md.append(f"- **{mode}**：作业站上没有这一模式的作业")
                summary.append((disp(st), mode, "—", "无该模式作业"))
                print(f"    {mode}: 无该模式作业")
                continue
            p = pool[0]
            picks[mode] = p
            summary.append((disp(st), mode, f"`prts://{p['id']}`", concl(p)))
            print(f"    {mode}首选 prts://{p['id']} ★{p['rating']} 赞{p['like']} 动作{p['actions']}"
                  f" — {(p['title'] or '')[:34]} [{concl(p)}]")
            md.append(f"- **{mode}首选**：`prts://{p['id']}` {p['title']} — {concl(p)}"
                      f"（★{p['rating']} 赞{p['like']} 动作{p['actions']} 薄弱环节{p['weak']}"
                      + ("；作者标自用" if p["flaky"] else "") + "）")
            if p["notes"]:
                md.append(f"  - 编队核对：{'／'.join(p['notes'])}")
            # 同模式备选（缺口档位相同的前 3 份）
            alts = [r for r in pool[1:] if rank(r)[:5] <= rank(p)[:5] or rank(r)[0] == 0][:3]
            if alts:
                md.append("  - 备选：" + "｜".join(
                    f"`prts://{a['id']}` {a['title'][:36]}（{label(a)}，★{a['rating']} 赞{a['like']}）"
                    for a in alts))
        if len(picks) == 2 and picks["普通"]["id"] == picks["突袭"]["id"]:
            md.append("- 注：普通/突袭通用作业，同一份跑两次即可")
        md.append("")
        print()
        report[st] = rows
        if args.dump_dir and picks:
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

    if args.dump_dir:
        for f in Path(args.dump_dir).glob("*.json"):
            if f.name not in dumped:
                f.unlink()

    md.append("## 汇总\n")
    md.append("| 关卡 | 模式 | 首选作业 | 结论 |")
    md.append("|---|---|---|---|")
    for name, mode, jid, c in summary:
        md.append(f"| {name} | {mode} | {jid} | {c} |")

    if miss_tally:
        md.append("\n## 缺干员（影响作业份数前 12）— 借好友助战或补练\n")
        print("\n缺干员（影响作业份数前 12）：")
        for nm, n in sorted(miss_tally.items(), key=lambda x: -x[1])[:12]:
            md.append(f"- {nm}：{n} 份作业点名需要（本号没有）")
            print(f"    {nm}: {n} 份作业需要")
    if gap_tally:
        md.append("\n## 补练度清单（影响作业份数前 12）\n")
        for (nm, hav, need), n in sorted(gap_tally.items(), key=lambda x: -x[1])[:12]:
            md.append(f"- {nm}：现在 {hav} → 作业要 {need}（影响 {n} 份）")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path = args.out.with_suffix(".md")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n报告：{args.out}\n人读版：{md_path}")
    if malist:
        mf = args.out.with_name(args.out.stem + "_malist.json")
        mf.write_text(json.dumps(malist, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"MAA 多作业列表：{mf}")


if __name__ == "__main__":
    main()
