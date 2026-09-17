"""筛作业 —— 极简执行器第 ② 步：按 box 给某一关的作业排序（缺几个）。

用法:
    python tools/list_jobs.py --stage act54side_ex07 --box data/boxes/box_751737728.json
    python tools/list_jobs.py --stage EX-7 --box data/boxes/box_751737728.json      # 也能吃 EX-7 这种写法

判据（用户 2026-09-17《极简执行器》规格）:
    ✅ 缺 0，能编满
    ⚠️ 缺 1-2，可能要借（MAA 能借助战补，实测 9036 借圣聆初雪一次通关 EX-7）
    ❌ 缺 3+ 或空壳（actions=[]，MAA 拿到会原地空转）

**不做**：不自动选、不自动跑、不写状态。
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "tools"))

import job_fit as JF                                           # noqa: E402


def stage_pattern(stage: str) -> str:
    """`act54side_ex07` 原样用；`EX-7`/`SR-8` 转成池子里的前缀写法。"""
    s = stage.strip()
    if s.upper().startswith("EX-"):
        return f"act54side_ex{int(s.split('-')[1]):02d}_"
    if s.upper().startswith("SR-"):
        return f"act54side_{int(s.split('-')[1]):02d}_"
    return s


def load_box_names(box_path: str) -> set[str]:
    d = json.loads(pathlib.Path(box_path).read_text(encoding="utf-8"))
    own = d.get("own") if isinstance(d, dict) else d
    out = set()
    for o in (own or []):
        if isinstance(o, dict) and o.get("name"):
            out.add(str(o["name"]))
        elif isinstance(o, str):
            out.add(o)
    return out


def op_count(d: dict) -> int:
    """这份作业实际会用到几个干员（部署过的名字去重）—— 给人一眼看规模。"""
    names = {a.get("name") for a in (d.get("actions") or [])
             if isinstance(a, dict) and a.get("type") == "Deploy" and a.get("name")}
    return len(names)


def main() -> None:
    ap = argparse.ArgumentParser(description="筛作业（极简执行器第 ② 步）")
    ap.add_argument("--stage", required=True, help="如 act54side_ex07 或 EX-7")
    ap.add_argument("--box", required=True, help="box 文件路径（scan_box.py 的产物）")
    a = ap.parse_args()

    box = load_box_names(a.box)
    pat = stage_pattern(a.stage)
    # 两种目录布局都认：池子式 `data/copilot/<pool>/<stage>_<id>.json` 与关卡式 `data/copilot/<stage>/<id>.json`
    files = sorted(set(glob.glob(str(PROJ / "data" / "copilot" / "*" / f"{pat}*.json")))
                   | set(glob.glob(str(PROJ / "data" / "copilot" / pat / "*.json"))))
    rows = []
    for p in files:
        try:
            d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        title = ((d.get("doc") or {}).get("title") or "").strip()
        if not (d.get("actions") or []):
            rows.append((99, pathlib.Path(p), "❌ 空壳（actions=[]）", title, 0))
            continue
        miss, dead, _t = JF.fit(p, box)
        miss = list(miss) + list(dead)
        mark = "✅ 缺 0" if not miss else (f"⚠️ 缺 {len(miss)}" if len(miss) <= 2 else f"❌ 缺 {len(miss)}")
        rows.append((len(miss), pathlib.Path(p), mark, title, op_count(d)))
    rows.sort(key=lambda r: (r[0], -r[4]))

    print(f"{a.stage} 候选（box: {pathlib.Path(a.box).stem.replace('box_', '')} ｜ {len(box)} 人）:")
    for n, p, mark, title, ops in rows:
        pool = p.parent.name
        extra = f"  {pool}" if p.parent.name not in (pat, a.stage) else ""
        print(f"  {mark:10s} {p.stem[:34]:36s} {ops:2d}人{extra}  {title[:26]}")
    if not rows:
        print("  （没找到候选：确认 --stage 写法，或池子里确实没有这一关的作业）")


if __name__ == "__main__":
    main()
