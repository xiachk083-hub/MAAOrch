#!/usr/bin/env python3
"""活动作业筛选（缺口优先 + 突袭覆盖）—— 一次性脚本，活动前缀可换。

为什么不直接跑 prts_event_pick.py --event <act>:
  1. 那份的 display_name() 写死了 act54side(SR)，换活动标题检索就废；
  2. 它的 screen_stage 要求 content.stage_name 严格等于 stage —— 作业站把
     「仅突袭」作业挂在 `<stage>#f#` 上，会整批漏掉（EX 关突袭全靠这批）；
  3. 它的 rank_key 把「查不到的要求（专精/模组）」排在质量之前 → 首选会落到
     ★0 赞1 的 2 动作「自用」占位作业上。本脚本改成：**缺口优先（缺人/缺练度/
     作者注明需手动/空作业），同档内按口碑与完整度排**，专精/模组只作风险提示。

判定口径直接 import 复用 prts_event_pick 的 evaluate。

用法:
    python tools/_event_user_pick.py --event act16side --letter GA --operbox data/box.json
    python tools/_event_user_pick.py --event act54side --letter SR --operbox data/box.json \
        --out data/copilot/sr54_user_pick.json --dump-dir data/copilot/sr54_picked
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

from prts_event_pick import API, evaluate, full_content, get, inline_content, load_box  # noqa: E402

WARN_TITLE = ("手动", "无法自动", "MAA做不了", "看视频", "卡轴", "无法代理",
              "半成品", "未测试", "已失效", "废弃")
FLAKY_TITLE = ("自用", "增补", "修改自用")

EVENT = "act16side"
LETTER = "GA"
LEDGER: dict = {}
PORT = ""


def skeleton(event: str) -> list[str]:
    """活动固定骨架：8 普通关 + 教学 + 8 EX"""
    return ([f"{event}_{i:02d}" for i in range(1, 9)] + [f"{event}_tr01"]
            + [f"{event}_ex{i:02d}" for i in range(1, 9)])


def disp(stage: str) -> str:
    m = re.match(rf"{re.escape(EVENT)}_(?:(tr|ex|s)(\d+)|(\d+))$", stage)
    if not m:
        return stage
    kind, num, plain = m.groups()
    if plain:
        return f"{LETTER}-{int(plain)}"
    return f"{LETTER}-{kind.upper()}-{int(num)}"


def query(stage: str, limit: int = 100) -> list[dict]:
    """按 stage_name 子串查（'act54side_ex03' 会把 '#f#' 的仅突袭作业一起带出来）"""
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


def extra_stages(known: set[str]) -> list[str]:
    """骨架之外的关卡名（个别活动有 SR-S-1 之类高难关）"""
    found: set[str] = set()
    try:
        d = get(f"{API}/copilot/query?limit=100&level_keyword={urllib.parse.quote(EVENT)}")
        for it in d["data"]["data"]:
            c = inline_content(it) or {}
            sn = c.get("stage_name") or ""
            if sn.startswith(EVENT) and sn.rstrip("#f#") not in known:
                found.add(sn.rstrip("#f#"))
    except Exception as e:
        print(f"  ! 探测额外关卡失败: {e}")
    return sorted(found)


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
        unleveled_penalty(r, box)
        r["ledger"], r["ledger_info"] = ledger_flags(c, box, LEDGER, PORT)
        r["raid_only"] = sn.endswith("#f#") or c.get("difficulty") == 2
        r["normal_only"] = c.get("difficulty") == 1 and not sn.endswith("#f#")
        if r["raid_only"]:
            r["raid"] = True
        r["flaky"] = any(w in (r["title"] or "") for w in FLAKY_TITLE)
        rows[it["id"]] = r
    return list(rows.values())


def deepen(rows: list[dict], n: int) -> None:
    for r in sorted(rows, key=rank)[:n]:
        c = full_content(r["id"])
        if not c:
            continue
        r["actions"] = len(c.get("actions") or [])
        r["broken"] = r["actions"] == 0
        for w in WARN_TITLE:
            if w in ((c.get("doc") or {}).get("title") or "") and w not in r["warn"]:
                r["warn"].append(w)


def ledger_flags(content: dict, box: dict, ledger: dict, port: str = "") -> tuple[list[str], list[str]]:
    """练度台账交叉核对（2026-09-15）：MAA 的干员识别读不到专精/模组，但
    「抄作业编队那一刻」的日志里有 —— tools/maa_mastery_ledger.py 把那些证据攒成了台账。

    返回 (硬信号, 提示)：
      硬信号（进排序，因为它真会毁掉这一把）——
        · 作业要 skill_level ≥ 8（专精）而台账记过该干员「技能等级不足」
        · 作业要 module > 0 而台账记过该干员「所需模组未解锁」
        · 台账记过「未拥有」而干员盒说持有 → 干员盒误报
      提示（只显示、不降档）——
        「等级不足 / 精英化不足」是**相对某份旧作业**的判定，作业之间门槛不同，
        拿它当通用短板会把整张表标红（2026-09-15 实测），所以只提示。
    """
    if not ledger:
        return [], []
    by_acc = ledger.get("by_account") or {}
    rec = (by_acc.get(port) if port else None) or ledger.get("by_op") or {}
    hard: list[str] = []
    info: list[str] = []
    rows: list[tuple[str, dict]] = []
    for o in content.get("opers") or []:
        rows.append((o.get("name") or "", o.get("requirements") or {}))
    for g in content.get("groups") or []:
        for c in g.get("opers") or []:
            rows.append((c.get("name") or "", c.get("requirements") or {}))
    for nm, req in rows:
        if not nm or not (box.get(nm) or {}).get("own"):
            continue
        d = rec.get(nm)
        reasons = set(d.keys()) if isinstance(d, dict) else set()
        if not reasons:
            continue
        if (req.get("skill_level") or 0) >= 8 and "技能等级不足" in reasons:
            hard.append(f"{nm} 专精存疑（作业要专{(req.get('skill_level') or 8) - 7}，台账记过技能等级不足）")
        if (req.get("module") or 0) > 0 and "所需模组未解锁" in reasons:
            hard.append(f"{nm} 模组未解锁（台账实锤，作业要模{req['module']}）")
        if "未拥有" in reasons:
            hard.append(f"{nm} 干员盒误报（MAA 报它未拥有）")
        for soft in ("等级不足", "精英化不足"):
            if soft in reasons:
                info.append(f"{nm} 台账记过{soft}（相对某份旧作业）")
    return sorted(set(hard)), sorted(set(info))


def unleveled_penalty(r: dict, box: dict) -> None:
    """干员盒里「有、但精0 Lv1」的 4★+ 干员 = 实际用不了，算缺口。

    2026-09-15 用户点出: GA-8 首选（作业 57500）点名要德克萨斯，而号里那张是
    精0 Lv1 —— 形同没有。原判定只看「有没有」，会把这类作业排成「能跑」。
    1~3★ 不罚（挂件位本来就是精0 低星），4★ 以上才罚。
    """
    for nm in list(r.get("used") or []):
        me = box.get(nm) or {}
        if (me.get("elite", 0) == 0 and me.get("level", 1) <= 1
                and (me.get("rarity") or 3) >= 4):
            if nm not in r["low"]:
                r["low"].append(nm)
            r["gaps"].append({"name": nm, "have": f"精0Lv{me.get('level', 1)}",
                              "need": "未练（需练到能用）"})


def rank(r: dict) -> tuple:
    """缺口优先 → 口碑/完整度 → 薄弱环节少。专精/模组（soft）不进排序，只作提示。"""
    clean = 0 if (not r["miss"] and not r["low"] and not r["warn"] and not r["broken"]
                  and (r["actions"] or 0) >= 3) else 1
    borrow = 0 if (len(r["miss"]) == 1 and not r["low"] and not r["warn"]) else 1
    return (clean, borrow, len(r["miss"]), len(r["low"]), len(r["warn"]),
            1 if r.get("ledger") else 0,
            1 if r["broken"] else 0, 1 if r["flaky"] else 0,
            -r["like"], -r["rating"], r["weak"])


def capable(r: dict, raid: bool) -> bool:
    return (not r["normal_only"]) if raid else (not r["raid_only"])


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
    if r["broken"]:
        return "空作业（作者只放了视频）"
    tail = []
    if r["miss"]:
        tail.append("缺" + ",".join(r["miss"][:3])
                    + ("（可借1人）" if len(r["miss"]) == 1 else "（多于1人，借不了）"))
    if r["low"]:
        tail.append("差练度" + ",".join(r["low"][:3]))
    if r["warn"]:
        tail.append("作者注明" + ",".join(r["warn"][:2]))
    if r["soft"]:
        tail.append("未核专精/模组" + ",".join(dict.fromkeys(r["soft"]))[:80])
    return "直接能跑" if not tail else "；".join(tail)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="活动前缀，如 act54side / act16side")
    ap.add_argument("--letter", required=True, help="游戏内关卡字母，如 SR / GA")
    ap.add_argument("--operbox", required=True, type=Path)
    ap.add_argument("--deep", type=int, default=20, help="每关给前几份拉正文")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--dump-dir", default="", help="把每关首选作业正文落盘（MAA 本地加载用）")
    ap.add_argument("--stages", default="", help="只跑这些 stage_name（逗号分隔，默认整场）")
    ap.add_argument("--probe-extra", action="store_true", help="额外探测骨架外的关卡名")
    ap.add_argument("--ledger", type=Path, default=PROJ / "data" / "mastery_ledger.json",
                    help="练度台账（tools/maa_mastery_ledger.py 产出）；缺文件则忽略")
    ap.add_argument("--ledger-port", default="",
                    help="只取该 adb 端口（如 16416）的台账，不传则跨端口合并")
    args = ap.parse_args()

    global EVENT, LETTER, LEDGER, PORT
    EVENT, LETTER = args.event, args.letter
    PORT = args.ledger_port
    try:
        if args.ledger and Path(args.ledger).exists():
            LEDGER = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
            print(f"练度台账：{Path(args.ledger).name}（{LEDGER.get('total', 0)} 条证据"
                  + (f"，端口 {PORT}" if PORT else "，跨端口合并") + "）")
    except Exception as e:
        print(f"! 台账读取失败（忽略）：{e}")

    box = load_box(args.operbox)
    e2 = sum(1 for o in box.values() if o.get("elite", 0) >= 2)
    print(f"干员箱 {args.operbox.name}（拥有 {len(box)} 名 / 精二 {e2}）｜{LETTER} {EVENT}\n")

    stages = ([x.strip() for x in args.stages.split(",") if x.strip()] if args.stages
              else skeleton(EVENT))
    if args.probe_extra and not args.stages:
        extra = extra_stages(set(stages))
        if extra:
            print(f"骨架外发现关卡：{extra}\n")
            stages += extra

    report: dict = {}
    md = [f"# {LETTER} 系列 作业推荐（按你的干员箱筛）\n",
          f"- 干员箱：`{args.operbox.name}`（拥有 {len(box)} 名 / 精二 {e2}）",
          f"- 生成：{time.strftime('%Y-%m-%d %H:%M')}",
          "- 口径：精英化/等级/潜能 **硬判**（干员箱有真值）；专精/模组查不到，只作风险提示",
          f"- 覆盖：{LETTER}-1~8 + 教学 + {LETTER}-EX-1~8（含作业站 `#f#` 仅突袭作业）",
          "- 排序：先看缺口（缺人/缺练度/作者注明需手动/空作业），同档按口碑与完整度\n"]
    summary: list[tuple[str, str, str, str]] = []
    miss_tally: dict[str, int] = {}
    gap_tally: dict[tuple, int] = {}
    malist: list[dict] = []
    dumped: set[str] = set()

    for st in stages:
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

        modes = (False, True) if re.match(rf"{re.escape(EVENT)}_ex\d+$", st) else (False,)
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
            if p.get("ledger"):
                print(f"      ⚠ 练度台账：{'；'.join(p['ledger'][:3])}")
                md.append(f"  - ⚠ **练度台账**：{'；'.join(p['ledger'])}")
            md.append(f"- **{mode}首选**：`prts://{p['id']}` {p['title']} — {concl(p)}"
                      f"（★{p['rating']} 赞{p['like']} 动作{p['actions']} 薄弱环节{p['weak']}"
                      + ("；作者标自用" if p["flaky"] else "") + "）")
            if p["notes"]:
                md.append(f"  - 编队核对：{'／'.join(p['notes'])}")
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
        md.append("\n## 缺干员（影响作业份数前 12）\n")
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
