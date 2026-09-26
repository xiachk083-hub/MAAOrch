#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改作业 · 本地轴生成器（2026-09-24，用户口径「抄不动就改作业」）

为什么需要它：这个号**专精零、无模组**，而作业站热门轴普遍要求「专10 模1」——
  ① MAA 直接判 `unusable`、连战斗都不开；
  ② 即使开了 `--ignore-requirements` 硬打，轴的**角色组**里第一个能满足条件的干员
     未必是我们练度最高的那个（例如「4格+长手」组首位是伊芙利特 精2Lv60，
     而我们手里有维什戴尔 精2Lv88）。

本工具做两件事，产出**本地作业**（不动作业站原文）：
  1. **放宽练度门槛**：把 `requirements` 里的 `skill_level` 压到当前可满足值、
     `module` 归零 ⇒ MAA 不再判 unusable；
  2. **按本号 box 重排角色组**：组内成员按（是否拥有 → 精英化 → 等级）排序，
     让 MAA 首选我们最强的那位；顺带把组里我们没练的挪到最后。
不改动作时序（location/direction/skill_usage 一律保持原样）—— 那是第三层，先不动。

用法:
    python tools/axis_patch.py --stage act18side_ex07 --id 72506
    python tools/axis_patch.py --stage act18side_ex07 --auto      # 自动挑排序第一的轴
产出: data/copilot/local/<stage>_<id>_local.json
跑法: python tools/maa_job_fallback.py --stage <stage> --local <产物> --ignore-requirements
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev  # noqa: E402  (保持与其它工具一致的导入习惯)

LOCAL_DIR = ROOT / "data" / "copilot" / "local"
SCREEN_DIR = ROOT / "data" / "copilot" / "screening"


def load_box() -> dict[str, dict]:
    files = sorted((ROOT / "data").glob("operbox*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("data/ 下没有 operbox*.json")
    d = json.load(open(files[0], encoding="utf-8"))
    return {o["name"]: o for o in (d.get("own") or [])}


def load_axis(stage: str, axis_id: str) -> tuple[dict, pathlib.Path]:
    """从 screening 缓存里读作业正文（prts_job_finder/sweep 落盘的那种）。"""
    p = SCREEN_DIR / f"{stage}_{axis_id}.json"
    if not p.exists():
        cands = sorted(SCREEN_DIR.glob(f"{stage}_*.json"))
        raise SystemExit(f"找不到 {p.name}；现有：{[c.name for c in cands[:5]]}")
    raw = json.load(open(p, encoding="utf-8"))
    content = raw.get("content") or raw
    if isinstance(content, str):
        content = json.loads(content)
    return content, p


def strength(box: dict, name: str) -> tuple:
    o = box.get(name)
    if not o:
        return (0, 0, 0)          # 没有 → 最后
    return (1, o.get("elite") or 0, o.get("level") or 0)


def relax(reqs: dict | None) -> dict | None:
    if not isinstance(reqs, dict):
        return reqs
    out = dict(reqs)
    if "skill_level" in out:
        out["skill_level"] = min(int(out["skill_level"]), 7)   # 7 = 未专精（技能 7 级）
    if "module" in out:
        out["module"] = 0
    if "level" in out:
        out["level"] = min(int(out["level"]), 60)
    if "elite" in out:
        out["elite"] = min(int(out["elite"]), 2)
    return out or None


def patch(content: dict, box: dict) -> tuple[dict, list[str]]:
    diff: list[str] = []
    c = json.loads(json.dumps(content))     # 深拷贝

    for op in c.get("opers") or []:
        before = op.get("requirements")
        op["requirements"] = relax(before)
        if before != op["requirements"]:
            diff.append(f"放宽 {op['name']}: {before} → {op['requirements']}")
        op["_你有的练度"] = (lambda o: f"精{o['elite']}Lv{o['level']}" if o else "未拥有")(box.get(op["name"]))

    for g in c.get("groups") or []:
        members = g.get("opers") or []
        for m in members:
            before = m.get("requirements")
            m["requirements"] = relax(before)
        order_before = [m["name"] for m in members]
        members.sort(key=lambda m: strength(box, m["name"]), reverse=True)
        order_after = [m["name"] for m in members]
        if order_before != order_after:
            diff.append(f"组「{g['name']}」顺序: {' → '.join(order_before)} ⇒ {' → '.join(order_after)}")
    return c, diff


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--id", default="")
    ap.add_argument("--auto", action="store_true", help="用 runtime/axis_rank.json 里排第一的轴")
    a = ap.parse_args()

    box = load_box()
    axis_id = a.id
    if a.auto or not axis_id:
        rank = json.load(open(ROOT / "runtime" / "axis_rank.json", encoding="utf-8"))
        ids = rank.get(f"{a.stage}|普通") or []
        if not ids:
            raise SystemExit(f"axis_rank.json 里没有 {a.stage}|普通")
        axis_id = str(ids[0]).split("_")[-1]
        print(f"自动选轴：prts://{axis_id}（P(3星) 排序第一）")

    content, src = load_axis(a.stage, axis_id)
    patched, diff = patch(content, box)
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    out = LOCAL_DIR / f"{a.stage}_{axis_id}_local.json"
    out.write_text(json.dumps(patched, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"源：{src.name}｜doc：{(content.get('doc') or {}).get('title', '')[:50]}")
    print(f"改后：{out}")
    for d in diff:
        print("  ·", d)
    if not diff:
        print("  （这份轴本来就没写练度要求，且角色组顺序已符合本号 box —— 无需改）")
    print(f"\n跑法：python tools/maa_job_fallback.py --emu 1 --stage {a.stage} --local \"{out}\" --ignore-requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
