#!/usr/bin/env python3
"""蓝材料（T3）刷取地点查询 — 基于 MAA stages.json 掉落表 + 企鹅物流真实掉率。

用法:
    python tools/material_stages.py                  # 全部蓝材料刷取表（AP 保底）
    python tools/material_stages.py 固源岩组         # 单个材料
    python tools/material_stages.py --search 盐      # 按关键词搜材料
    python tools/material_stages.py --rate           # 企鹅物流真实掉率最优关（期望 AP/个）
    python tools/material_stages.py --rate --refresh # 强制重拉企鹅物流数据

数据:
    stages.json (608 关掉落) + item_index.json (物品名) — MAA 自带
    企鹅物流 matrix API (掉率 = quantity/times) — 缓存 %TEMP%/ps_matrix.json
规则: NORMAL_DROP=保底优先; EXTRA_DROP/SPECIAL_DROP=概率/特殊;
      常驻主线(main/tough/sub/wk 前缀 stageId) 与 活动关(act 前缀) 分开标注。
"""
from __future__ import annotations
import argparse
import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
RES = PROJ / "services" / "maa" / "source" / "resource"
PS_MATRIX_CACHE = Path(os.environ.get("TEMP", "/tmp")) / "ps_matrix.json"
PS_API = "https://penguin-stats.io/PenguinStats/api/v2/result/matrix?show_closed_zones=false&server=CN"

# 蓝材料（T3）标准清单 —— 正常要刷的 17 个核心材料
# （精炼溶剂/晶体电路掉率极低(0.04)不正常刷，另列 SPECIAL）
BLUE_MATERIALS = [
    "固源岩组", "糖组", "聚酸酯组", "异铁组", "酮凝集组", "装置",
    "扭转醇", "轻锰矿", "研磨石", "RMA70-12", "凝胶", "炽合金",
    "晶体元件", "半自然溶剂", "化合切削液", "环烃聚质", "转质盐组",
]
SPECIAL_LOW_RATE = ["精炼溶剂", "晶体电路"]  # 掉率极低，不建议硬刷


def load() -> tuple[dict, list]:
    items = json.loads((RES / "item_index.json").read_text(encoding="utf-8"))
    stages = json.loads((RES / "stages.json").read_text(encoding="utf-8"))
    return items, stages


def fetch_matrix(refresh: bool = False) -> list:
    """企鹅物流掉落矩阵（掉率 = quantity/times，缓存到 %TEMP%）。"""
    if not refresh and PS_MATRIX_CACHE.exists():
        return json.loads(PS_MATRIX_CACHE.read_text(encoding="utf-8"))["matrix"]
    req = urllib.request.Request(PS_API, headers={"User-Agent": "MAAOrch/1.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=60,
                                             context=ssl.create_default_context()).read())
    PS_MATRIX_CACHE.write_text(json.dumps(data), encoding="utf-8")
    return data["matrix"]


def is_resident(stage_id: str) -> bool:
    """常驻关卡（主线/支线/周常）vs 活动关。"""
    return stage_id.startswith(("main_", "tough_", "sub_", "wk_"))


def query_material(items: dict, stages: list, name: str) -> dict:
    iid = next((k for k, v in items.items() if v["name"] == name), None)
    if not iid:
        return {"name": name, "found": False}
    drops = []
    for st in stages:
        for d in st.get("dropInfos", []):
            if d.get("itemId") == iid:
                drops.append({"code": st["code"], "ap": st["apCost"],
                              "type": d["dropType"],
                              "resident": is_resident(st["stageId"])})
    return {"name": name, "found": True, "drops": drops}


def rate_report(items: dict, stages: list, matrix: list) -> str:
    """企鹅物流掉率报告：活动最优（参考）+ 常驻关完整排行（长期刷取依据）。"""
    name2id = {v["name"]: k for k, v in items.items()}
    stage_map = {st["stageId"]: (st["code"], st["apCost"]) for st in stages}
    out = [f"# 蓝材料刷取地点（企鹅物流真实掉率，{len(matrix)} 条样本）", "",
           "期望 AP/个 = apCost / (quantity/times)；样本 <500 剔除", ""]
    for name in BLUE_MATERIALS + SPECIAL_LOW_RATE:
        if name in SPECIAL_LOW_RATE:
            out.append("")
        iid = name2id.get(name)
        if not iid:
            continue
        rows = []
        agg: dict = {}  # stageId -> [quantity, times]（多时间区间累加）
        for row in matrix:
            if row["itemId"] != iid:
                continue
            a = agg.setdefault(row["stageId"], [0, 0])
            a[0] += row["quantity"]
            a[1] += row["times"]
        for sid, (q, t) in agg.items():
            if t < 500:
                continue
            st = stage_map.get(sid)
            if not st:
                continue
            rate = q / t
            if rate <= 0 or rate > 1.05:  # 掉率异常剔除
                continue
            rows.append((st[1] / rate, st[0], rate, t, is_resident(sid)))
        # code 去重（主线普通/困难双记录合并，保留更优）
        by_code: dict = {}
        for r in rows:
            if r[1] not in by_code or r[0] < by_code[r[1]][0]:
                by_code[r[1]] = r
        rows = sorted(by_code.values(), key=lambda x: x[0])
        if not rows:
            out.append(f"**{name}**: (无有效数据)")
            continue
        rows.sort(key=lambda x: x[0])
        act = [r for r in rows if not r[4]]
        res = [r for r in rows if r[4]]
        parts = [f"**{name}**"]
        if act:
            a = act[0]
            parts.append(f"活动最优 {a[1]}({a[0]:.0f}AP/个, 掉率{a[2]:.2f})")
        if res:
            top = res[:3]
            parts.append("常驻: " + " > ".join(
                f"{c}({ap:.0f}AP/个, {rate:.2f})" for ap, c, rate, _, _ in top))
        else:
            parts.append("⚠ 无常驻关掉落")
        out.append(" | ".join(parts))
    out.append("")
    out.append("注: 常驻排行按期望 AP/个升序；活动关结束即消失，长期刷取看常驻列")
    return "\n".join(out)


def render(d: dict) -> str:
    if not d["found"]:
        return f"**{d['name']}**: 数据中未找到（可能在商店/合成获取）"
    drops = d["drops"]
    if not drops:
        return f"**{d['name']}**: 无关卡掉落记录"
    # 常驻 NORMAL 保底
    resident_normal = sorted([x for x in drops if x["resident"] and x["type"] == "NORMAL_DROP"],
                             key=lambda x: x["ap"])
    # 常驻 EXTRA
    resident_extra = sorted([x for x in drops if x["resident"] and x["type"] == "EXTRA_DROP"],
                            key=lambda x: x["ap"])
    # 活动关
    act_drops = sorted([x for x in drops if not x["resident"]], key=lambda x: x["ap"])
    parts = []
    if resident_normal:
        parts.append("常驻保底: " + ", ".join(f"{x['code']}({x['ap']}AP)" for x in resident_normal[:3]))
    if resident_extra:
        parts.append("常驻额外: " + ", ".join(f"{x['code']}({x['ap']}AP)" for x in resident_extra[:3]))
    if act_drops:
        act_n = [x for x in act_drops if x["type"] == "NORMAL_DROP"]
        src = act_n if act_n else act_drops
        parts.append(f"活动关: " + ", ".join(f"{x['code']}({x['ap']}AP)" for x in src[:3]))
    if not any(x["type"] == "NORMAL_DROP" for x in drops):
        parts.append("⚠ 仅额外/特殊掉落")
    return f"**{d['name']}**: " + " | ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="蓝材料刷取地点查询")
    ap.add_argument("material", nargs="?", help="材料名（不传=全部蓝材料）")
    ap.add_argument("--search", help="按关键词搜材料名")
    ap.add_argument("--rate", action="store_true", help="企鹅物流真实掉率报告")
    ap.add_argument("--refresh", action="store_true", help="强制重拉企鹅物流数据")
    args = ap.parse_args()

    items, stages = load()
    id2name = {k: v["name"] for k, v in items.items()}

    if args.rate:
        try:
            matrix = fetch_matrix(args.refresh)
        except Exception as e:
            sys.exit(f"企鹅物流数据拉取失败: {e}")
        print(rate_report(items, stages, matrix))
        return

    if args.rate:
        try:
            matrix = fetch_matrix(args.refresh)
        except Exception as e:
            sys.exit(f"企鹅物流数据拉取失败: {e}")
        print(rate_report(items, stages, matrix))
        return

    if args.search:
        hits = [n for n in id2name.values() if args.search in n]
        print("命中材料:", ", ".join(hits[:20]) if hits else "（无）")
        return

    if args.material:
        print(render(query_material(items, stages, args.material)))
        return

    print(f"# 蓝材料刷取地点（数据: MAA stages.json {len(stages)} 关）\n")
    for name in BLUE_MATERIALS:
        print("- " + render(query_material(items, stages, name)))


if __name__ == "__main__":
    main()
