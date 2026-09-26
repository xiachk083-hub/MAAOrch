#!/usr/bin/env python3
"""把我们自己算出来的打法变成一份 MAA copilot 作业（本地文件，不抄别人）。

定位（2026-09-20 用户："我要的是自己打不用代理"）:
    「我们算落点 → 生成作业 → MAA 当手执行」这条链的**最后一环生成器**。
    作业的轴是我们的（封路口 + 输出 + 治疗/替补都由我们算），MAA 只负责点格子与放技能；
    copilot 路径**不会勾「代理指挥」**，所以满足"不用代理、不抄作业"。

为什么要有闸（三条 fail-closed，缺任一条就拒绝出作业）:
    ① 每个 location 必须在 Tile-Pos 里 buildableType != 0
       —— 坐标有两套（关卡 json 行 0 在最**下**、Tile-Pos 行 0 在最**上**），
          2026-09-20 实测 4220 个真落点：Tile-Pos 索引命中 99.5%、另一套只有 74.5%，
          所以作业 location 一律用 Tile-Pos 索引。落错格 = 整份作业白跑。
    ② 每个槽位的候选干员必须**真在 box 里**（按 operbox 文件，不猜）
       —— 没这个人 MAA 编队就缺卡，跑起来是"编队不满"而不是报错，很难看出来。
    ③ 只写 790 份真作业里出现过的字段（Deploy/SpeedUp/SkillDaemon + direction 取值）

用法:
    python tools/own_axis_job.py --plan plans/CE-6_v1.json --box data/operbox_<tag>.json \
        --out data/copilot/CE-6_ours_v1.json [--stage-id wk_melee_6]
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
TILEPOS_DIRS = [
    "D:/Xiach/M-A/MAA/*/resource/Arknights-Tile-Pos",
    "C:/Users/*/M-A/MAA/*/resource/Arknights-Tile-Pos",
    str(PROJ / "services" / "maa" / "*" / "resource" / "Arknights-Tile-Pos"),
]
DIRS = ("Right", "Left", "Up", "Down", "None")


def find_tilepos(stage_id: str) -> pathlib.Path | None:
    for pat in TILEPOS_DIRS:
        for d in glob.glob(pat):
            for f in glob.glob(str(pathlib.Path(d) / f"{stage_id}*.json")):
                if "#f#" not in f:
                    return pathlib.Path(f)
    return None


def load_box(path: pathlib.Path) -> set[str]:
    """兼容两种 box 格式：{"own":[...]} 与 扁平 [...]（stage_value 只吃后者）。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = d["own"] if isinstance(d, dict) else d
    return {o["name"] for o in rows if isinstance(o, dict) and o.get("own") and o.get("name")}


def main() -> int:
    ap = argparse.ArgumentParser(description="按我们自己的打法生成 MAA copilot 作业")
    ap.add_argument("--plan", required=True, type=pathlib.Path, help="打法 JSON（槽位 + 落点 + 朝向）")
    ap.add_argument("--box", required=True, type=pathlib.Path, help="operbox 文件（用来判「真有这个干员」）")
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--stage-id", default="", help="MAA stageId（默认用 plan 里的）")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    stage_name = plan["stage_name"]
    stage_id = args.stage_id or plan["stage_id"]

    tp_file = find_tilepos(stage_id)
    if tp_file is None:
        sys.exit(f"✗ 找不到 Tile-Pos：{stage_id}（落点无法验证 ⇒ 拒绝生成）")
    tp = json.loads(tp_file.read_text(encoding="utf-8"))
    T = tp["tiles"]
    H, W = len(T), len(T[0])

    box = load_box(args.box)
    if not box:
        sys.exit("✗ box 是空的 ⇒ 拒绝生成")

    # ---- 闸① + 闸②：逐槽位验 ----
    bad = []
    for s in plan["slots"]:
        x, y = s["location"]
        if not (0 <= x < W and 0 <= y < H):
            bad.append(f"{s['group']}: 落点 [{x},{y}] 越界（地图 {W}x{H}）")
            continue
        cell = T[y][x]
        if not cell.get("buildableType", 0):
            bad.append(f"{s['group']}: 落点 [{x},{y}] 不可部署"
                       f"（buildableType={cell.get('buildableType')} heightType={cell.get('heightType')}）")
        if s["direction"] not in DIRS:
            bad.append(f"{s['group']}: 朝向 {s['direction']} 不是 {DIRS}")
        if not [o for o in s["opers"] if o in box]:
            bad.append(f"{s['group']}: 候选 {s['opers']} 一个都不在 box 里")
    if bad:
        sys.exit("✗ 拒绝生成，以下槽位没通过:\n  - " + "\n  - ".join(bad))

    # ---- 组装（只写真作业里出现过的字段）----
    groups, actions = [], [{"type": "SpeedUp"}]
    for s in plan["slots"]:
        groups.append({"name": s["group"], "opers": [{"name": n} for n in s["opers"]]})
        actions.append({"type": "Deploy", "name": s["group"], "location": list(s["location"]),
                        "direction": s["direction"]})
    actions.append({"type": "SkillDaemon"})          # 技能好了就开（不用别人的轴）
    job = {
        # ⚠️ 必须写**内部 stageId**（wk_melee_6），不是界面编号（CE-6）：
        # 2026-09-20 实测 `stage_name: "CE-6"` → "Failed to find Tile-Pos file for CE-6"；
        # 换成 "wk_melee_6" → `[Copilot] Unstarted`（= 解析通过）。MAA 按 stageId 查 Tile-Pos。
        "stage_name": stage_id,
        "minimum_required": "v4.0.0",
        "doc": {"title": plan.get("title", f"{stage_name} 自研轴"),
                "details": plan.get("note", "")},
        "opers": [],
        "groups": groups,
        "actions": actions,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"✓ 已写 {args.out}")
    print(f"  关卡 {stage_name}（stageId {stage_id}）／地图 {W}x{H} ← {tp_file.name}")
    print(f"  干员池校验：box {len(box)} 人 ✓ 全部槽位候选都点名验过")
    for s in plan["slots"]:
        x, y = s["location"]
        who = [o for o in s["opers"] if o in box]
        print(f"    {s['group']:<10} → [{x},{y}] {s['direction']:<5} 首选 {who[0]}"
              f"（备选 {'/'.join(who[1:]) or '无'}）"
              f" 格型 buildable={T[y][x].get('buildableType')} height={T[y][x].get('heightType')}")
    print(f"  动作 {len(actions)} 个：{actions[0]['type']} + {len(plan['slots'])}×Deploy + SkillDaemon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
