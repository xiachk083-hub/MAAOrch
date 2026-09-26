#!/usr/bin/env python3
"""抄作业微调（换干员 / 修悬空引用）—— 让作业跟号里的干员对上。

为什么需要:
  1. 作业是按作者的号写的。号里没有或没练点名干员时，MAA 自动编队拿不到人 →
     直接进行不了（2026-09-15 用户: GA-8 作业 57500 点名德克萨斯，号里精0 Lv1，
     卡住跑不动；而这个位置只是产费先锋，换同职业能打）。
  2. 作业站上不少作业是「魔改未测」的：动作里会留下旧稿干员名（那个名字不在
     opers 里）。MAA 对不在队伍里的干员会跳过该步 → 依赖这步的策略（比如
     「先撤开场先锋腾出格子」）就崩了。本工具把这类悬空引用一并重定向。

用法:
  # 换个开场先锋（名字在 opers 和所有动作里同步替换）
  python tools/copilot_patch_oper.py --job 81591 --from 凛御银灰 --to 桃金娘 \\
      --skill 1 --skill-usage 1 --out data/copilot/ga16_adapted/ga8_patch.json

  # 顺带把动作里引用的「不在队伍中」的名字改到指定干员（默认改成 --to）
  python tools/copilot_patch_oper.py --job 81591 --fix-dangling-only \\
      --dangling-to 凛御银灰 --out data/copilot/ga16_adapted/ga8_fixed.json

  # 作业已存在本地文件
  python tools/copilot_patch_oper.py --file data/copilot/ga16_picked/act16side_08_81591.json ...
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

PROJ = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0"}
BATTLE_DATA = PROJ / "services" / "maa" / "source" / "resource" / "battle_data.json"


def load_job(job_id: int | None, path: pathlib.Path | None) -> dict:
    if path:
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(f"https://prts.maa.plus/copilot/get/{job_id}", headers=UA)
    d = json.load(urllib.request.urlopen(req, timeout=25))["data"]
    c = d.get("content")
    return json.loads(c) if isinstance(c, str) else c


def summon_names(cop_names: list[str]) -> set[str]:
    """作业里可能出现的召唤物/棋子名（令的「弦惊」这类），从 battle_data 的 tokens 拿。

    坑（2026-09-15 实测）：tokens 字段两种写法都有 —— 有的是 token 的 id
    （chars 里的键），有的直接就是名字（令: ['“清平”','“逍遥”','“弦惊”']）。
    两种都要认，否则召唤物会被当成悬空引用被替换掉，整份作业报废。
    """
    try:
        chars = json.loads(BATTLE_DATA.read_text(encoding="utf-8"))["chars"]
    except Exception:
        return set()
    by_name = {v.get("name"): v for v in chars.values()}
    out: set[str] = set()
    for nm in cop_names:
        for t in (by_name.get(nm) or {}).get("tokens") or []:
            if not isinstance(t, str):
                continue
            out.add(chars[t]["name"] if t in chars else t)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="抄作业换干员 / 修悬空引用")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--job", type=int, help="作业站 id")
    src.add_argument("--file", type=pathlib.Path, help="本地作业 json")
    ap.add_argument("--from", dest="src_name", help="要换掉的干员名")
    ap.add_argument("--to", dest="dst_name", help="换成哪个干员")
    ap.add_argument("--skill", type=int, help="替换后干员的技能序号（1/2/3）")
    ap.add_argument("--skill-usage", type=int, help="替换后干员的技能使用方式（0 手动 1 自动）")
    ap.add_argument("--fix-dangling-only", action="store_true",
                    help="不换人，只修动作里引用了「不在队伍中」的名字")
    ap.add_argument("--dangling-to", default="",
                    help="悬空引用改成谁（默认用 --to；--fix-dangling-only 时必填）")
    ap.add_argument("--add", action="append", default=[],
                    help="把某干员补进队伍（作业动作点名了他、却不在 opers 里）")
    ap.add_argument("--direction", action="append", default=[], metavar="干员=方向",
                    help="改某干员所有 Deploy 动作的朝向（Up/Down/Left/Right），可重复")
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--note", default="", help="写进 doc.title 的尾注，默认自动生成")
    args = ap.parse_args()

    job = load_job(args.job, args.file)
    opers = job.setdefault("opers", [])
    squad_before = [o.get("name") for o in opers]
    changes: list[str] = []

    # 1) 换人：opers 里改名（原有技能字段保留，除非显式覆盖）
    if args.src_name and args.dst_name:
        hit = False
        for o in opers:
            if o.get("name") == args.src_name:
                o["name"] = args.dst_name
                hit = True
                if args.skill is not None:
                    o["skill"] = args.skill
                if args.skill_usage is not None:
                    o["skill_usage"] = args.skill_usage
        if not hit:
            print(f"! opers 里没有 {args.src_name}", file=sys.stderr)
        else:
            changes.append(f"opers: {args.src_name} → {args.dst_name}"
                           + (f"（技能{args.skill}" if args.skill else "")
                           + (f"/usage {args.skill_usage}）" if args.skill_usage is not None else ""))
        for a in job.get("actions") or []:
            if a.get("name") == args.src_name:
                a["name"] = args.dst_name
                changes.append(f"action {a.get('type')} @{a.get('location')}: {args.src_name} → {args.dst_name}")

    # 2) 补人
    for nm in args.add:
        if nm and nm not in [o.get("name") for o in opers]:
            opers.append({"name": nm})
            changes.append(f"opers += {nm}")

    # 2b) 改朝向（2026-09-15 用户：102748 里的阿斯卡纶「朝上放会好一点」）
    for spec in args.direction:
        if "=" not in spec:
            print(f"! --direction 要写成「干员=方向」，忽略：{spec}", file=sys.stderr)
            continue
        nm, direction = (x.strip() for x in spec.split("=", 1))
        if direction not in ("Up", "Down", "Left", "Right"):
            print(f"! 方向只能是 Up/Down/Left/Right，忽略：{spec}", file=sys.stderr)
            continue
        n = 0
        for a in job.get("actions") or []:
            if a.get("type") == "Deploy" and a.get("name") == nm and a.get("direction"):
                if a["direction"] != direction:
                    changes.append(f"Deploy {nm} @{a.get('location')}: 朝向 {a['direction']} → {direction}")
                    a["direction"] = direction
                    n += 1
        if not n:
            print(f"! 没找到 {nm} 的 Deploy 动作（或原本就是这个朝向）", file=sys.stderr)

    # 3) 悬空引用：动作里出现、但既不在队伍、也不是召唤物的名字
    squad = [o.get("name") for o in opers]
    summons = summon_names(squad)
    target = args.dangling_to or args.dst_name
    dangling = sorted({a.get("name") for a in (job.get("actions") or [])
                       if a.get("name") and a.get("name") not in squad and a.get("name") not in summons})
    for nm in dangling:
        if args.fix_dangling_only and not target:
            print(f"! 悬空引用 {nm} 未处理（--dangling-to 没给）", file=sys.stderr)
            continue
        if not target:
            continue
        n = 0
        for a in job.get("actions") or []:
            if a.get("name") == nm:
                a["name"] = target
                n += 1
        changes.append(f"悬空引用 {nm} × {n} 处 → {target}")

    if not changes:
        print("没有任何改动（检查 --from/--to 的名字是否和作业里的写法一致）")

    note = args.note or "（改：" + "；".join(c.split(":")[-1].strip() for c in changes[:2]) + "）"
    doc = job.setdefault("doc", {})
    doc["title"] = (doc.get("title") or "") + note
    doc["details"] = ((doc.get("details") or "") + f"\n[本地改] 原始作业来自 prts.maa.plus，改动："
                      + "；".join(changes)).strip()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"改前队伍: {squad_before}")
    print(f"改后队伍: {[o.get('name') for o in opers]}")
    for _, c in enumerate(changes, 1):
        print(f"  {_} {c}")
    print(f"\n输出：{args.out}")

    # 自检：动作里是否还有不在队伍、也不是召唤物的名字
    squad2 = [o.get("name") for o in opers]
    summons2 = summon_names(squad2)
    left = sorted({a.get("name") for a in (job.get("actions") or [])
                   if a.get("name") and a.get("name") not in squad2 and a.get("name") not in summons2})
    print(f"残留悬空引用：{left or '无'}")
    print(f"召唤物（豁免）：{sorted(summons2) or '无'}")


if __name__ == "__main__":
    main()
