#!/usr/bin/env python3
"""MAA 点击位置画像 — 输入任务名或坐标，一条龙回答四层认知:

    L1 是什么    : 模板库认出（image_recognizer）
    L2 长什么样  : 像素特征（尺寸/金色占比/密度/主色 + 元素类型）
    L3 为什么点那 : 游戏 UI 功能区（布局地图 5 大区域）+ 任务职责
    L4 点了会怎样 : next/exceededNext 行为链 + 引用它的任务

用法:
    python tools/probe_element.py UsePrts-Annihilation     # 按任务名
    python tools/probe_element.py StartButton1
    python tools/probe_element.py --coord 877,532          # 按坐标（1280×720）
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

from task_graph import load_tasks, resolve_task, _describe  # noqa: E402

# 游戏 UI 功能区（与 docs/MAA_UI_LAYOUT.md 一致；顺序=优先级，按钮区优先于弹窗区）
REGIONS = [
    ("右中 · 代理/详情按钮区", 860, 470, 420, 150,
     "关卡详情页代理/扫荡按钮（全权委托、代理指挥）"),
    ("右下 · 主确认按钮区", 860, 580, 420, 140,
     "开始行动/切换/确认类按钮，OCR 多词任一命中"),
    ("右上 · HUD 状态区", 960, 0, 320, 200,
     "战斗标记/理智数字/跳过动画/盲区（点击无害区 ClickCorner）"),
    ("左上 · 返回/设置区", 0, 0, 320, 200,
     "全游戏统一的返回箭头/设置入口；Return.png 双模板兼容换肤"),
    ("中央 · 弹窗确认区", 520, 340, 460, 280,
     "PopupConfirm 弹窗，一个模板被掉线/返回/刷新等 5+ 域复用（覆盖部分按钮区）"),
]


def find_region(cx: int, cy: int) -> str:
    for name, x, y, w, h, desc in REGIONS:
        if x <= cx < x + w and y <= cy < y + h:
            return f"{name} — {desc}"
    return "布局地图外区域（低频点击/专用界面）"


def _refs_of(tasks: dict, name: str) -> list[str]:
    """谁在 next/sub/exceededNext 里引用该任务。"""
    out = []
    for tname, t in tasks.items():
        for f in ("next", "sub", "exceededNext", "onErrorNext"):
            v = t.get(f) or []
            if isinstance(v, list) and any(
                    isinstance(r, str) and (r == name or r.endswith("@" + name))
                    for r in v):
                out.append(tname)
    return out[:5]


def probe_task(tasks: dict, name: str) -> str:
    resolved = resolve_task(tasks, name)
    if resolved is None:
        return f"任务 {name} 不存在"
    definition, base = resolved
    roi = definition.get("roi")
    cx = cy = None
    if roi and len(roi) == 4:
        cx, cy = roi[0] + roi[2] // 2, roi[1] + roi[3] // 2

    lines = [f"# {name}（基任务 {base}）", ""]
    lines.append("## L1 · 是什么（职责）")
    lines.append(f"- {_describe(definition)}")
    doc = definition.get("Doc") or definition.get("doc")
    if doc:
        lines.append(f"- Doc: {str(doc)[:120]}")
    lines.append("")

    lines.append("## L2 · 长什么样（像素特征）")
    tpl = definition.get("template")
    tpl_name = None
    if isinstance(tpl, str) and tpl and tpl != "empty.png":
        tpl_name = tpl
    elif not tpl and name + ".png":
        cand = name + ".png"
        if (PROJ / "services" / "maa" / "source" / "resource" / "template").rglob(cand):
            tpl_name = cand
    if tpl_name:
        found = list((PROJ / "services" / "maa" / "source" / "resource" / "template").rglob(tpl_name))
        if found:
            p = found[0]
            try:
                from ui_element_classify import classify
                r = classify(str(p))
                lines.append(f"- 模板图: `{p.relative_to(PROJ)}`（{r['features']['w']}×{r['features']['h']}）")
                lines.append(f"- 元素类型: {r['verdict']}")
                lines.append(f"- 特征: 内容密度 {r['features']['content_density']} | "
                             f"边缘密度 {r['features']['edge_density']} | "
                             f"金色占比 {r['features']['gold_ratio']} | "
                             f"主色数 {r['features']['n_colors']}")
                lines.append(f"- 证据: {'; '.join(r['evidence'])[:150]}")
            except Exception as e:
                lines.append(f"- 模板图: `{p.relative_to(PROJ)}`（特征分析失败: {e}）")
        else:
            lines.append(f"- 模板图: {tpl_name}（模板库未找到）")
    else:
        lines.append("- 无模板图（OCR 识别 / 盲点动作 / 状态断言）")
    lines.append("")

    lines.append("## L3 · 为什么点那（布局语义）")
    if cx is not None:
        lines.append(f"- 点击位置: roi [{roi[0]},{roi[1]},{roi[2]},{roi[3]}] 中心 ({cx},{cy})")
        lines.append(f"- 功能区: {find_region(cx, cy)}")
    else:
        lines.append("- 无固定 roi（全屏/继承）")
    if base != name:
        lines.append(f"- 继承自: {base}（@ 上下文前缀传播）")
    lines.append("")

    lines.append("## L4 · 点了会怎样（行为链）")
    for f in ("next", "exceededNext", "onErrorNext", "sub"):
        v = definition.get(f)
        if v:
            lines.append(f"- {f}: {', '.join(str(x) for x in v[:8])}")
    refs = _refs_of(tasks, name)
    if refs:
        lines.append(f"- 被引用: {', '.join(refs)}")
    return "\n".join(lines)


def probe_coord(tasks: dict, x: int, y: int) -> str:
    lines = [f"# 坐标 ({x},{y}) 画像", ""]
    lines.append(f"## 功能区: {find_region(x, y)}")
    lines.append("")
    hits = []
    for name, t in tasks.items():
        roi = t.get("roi")
        if roi and len(roi) == 4:
            rx, ry, rw, rh = roi
            if rx <= x < rx + rw and ry <= y < ry + rh:
                hits.append((name, t))
    # 全屏/大半屏 ROI 是扫描窗，标出并排后；小 ROI（精确定位）优先
    hits.sort(key=lambda ht: ht[1].get("roi", [0, 0, 1280, 720])[2]
              * ht[1].get("roi", [0, 0, 1280, 720])[3])
    screen = 1280 * 720
    small = [h for h in hits if h[1].get("roi", [0, 0, 1280, 720])[2]
             * h[1].get("roi", [0, 0, 1280, 720])[3] < screen * 0.5]
    lines.append(f"## 精确定位该坐标的 roi（{len(small)} 个任务，小 ROI 优先）")
    for name, t in small[:12]:
        lines.append(f"- `{name}` [{t.get('action', 'DoNothing')}] {_describe(t)}")
    big = len(hits) - len(small)
    if big:
        lines.append(f"（另有 {big} 个全屏/大 ROI 扫描任务覆盖该坐标，如 ClickStageName）")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="MAA 点击位置画像（四层认知）")
    ap.add_argument("task", nargs="?", help="任务名")
    ap.add_argument("--coord", help="坐标 x,y（1280×720）")
    ap.add_argument("--source", help="tasks.json 路径")
    args = ap.parse_args()

    tasks = load_tasks(args.source)
    if args.coord:
        x, y = (int(v) for v in args.coord.split(","))
        print(probe_coord(tasks, x, y))
    elif args.task:
        print(probe_task(tasks, args.task))
    else:
        sys.exit("需要任务名或 --coord")


if __name__ == "__main__":
    main()
