#!/usr/bin/env python3
"""MAA UI 布局逆向工具 — 把任务的 ROI/点击点聚合为 1280×720 游戏界面布局地图。

回答「为什么点那里」：MAA 的每个 roi/specificRect 都是开发者按游戏 UI 实际
布局手工标注的，全部聚合后就是游戏界面的布局镜像（哪个区域有什么按钮/图标）。

用法:
    python tools/ui_layout_map.py                    # 8×8 网格布局地图（markdown）
    python tools/ui_layout_map.py --grid 4           # 4×4 网格（更粗）
    python tools/ui_layout_map.py --region 877,532   # 查询该坐标被谁点、识别什么
    python tools/ui_layout_map.py --top 40           # 点击最密集的 40 个区域
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

from task_graph import load_tasks, resolve_task, _describe  # noqa: E402

W, H = 1280, 720  # 基准分辨率


def collect_points(tasks: dict) -> list[dict]:
    """收集所有"在哪里点/识别什么"的数据点。"""
    points = []
    for name, t in tasks.items():
        action = t.get("action", "DoNothing")
        # 点击位置：ClickSelf → roi 区域；ClickRect → specificRect
        rect = None
        if action == "ClickRect" and t.get("specificRect"):
            rect = t["specificRect"]
        elif action in ("ClickSelf", "DoNothing") and t.get("roi"):
            rect = t["roi"]
        if rect and len(rect) == 4:
            points.append({
                "task": name, "action": action, "rect": tuple(rect),
                "algo": t.get("algorithm", "MatchTemplate"),
                "cond": _describe(t),
                "template": t.get("template", ""),
            })
    return points


def grid_of(rect: tuple, gx: int, gy: int) -> tuple:
    x, y, w, h = rect
    cx, cy = (x + w // 2) // (W // gx), (y + h // 2) // (H // gy)
    return (min(cx, gx - 1), min(cy, gy - 1))


def render_grid(points: list[dict], gx: int, gy: int, max_per_cell: int = 6) -> str:
    cells: dict = defaultdict(list)
    for p in points:
        cells[grid_of(p["rect"], gx, gy)].append(p)
    out = [f"# MAA UI 布局地图（{gx}×{gy} 网格，1280×720 基准）", ""]
    out.append("> 聚合来源：全部任务 roi(487) + specificRect(79) 的点击/识别点")
    out.append("> 每格列出：识别的模板/文字 + 动作 + 代表任务")
    out.append("")
    out.append("```")
    # 字符画：点击密度热图
    max_count = max((len(v) for v in cells.values()), default=1)
    for gyi in range(gy):
        row = []
        for gxi in range(gx):
            n = len(cells.get((gxi, gyi), []))
            ch = " .:+#@"[min(n * 5 // max(1, max_count), 5)] if n else " "
            row.append(ch)
        out.append("|" + "|".join(row) + "|")
    out.append("```")
    out.append("")
    for (gxi, gyi), items in sorted(cells.items(), key=lambda kv: -len(kv[1])):
        x0, y0 = gxi * (W // gx), gyi * (H // gy)
        out.append(f"## [{gxi},{gyi}] 区域 ({x0},{y0}) — 点击/识别点 {len(items)} 个")
        for p in items[:max_per_cell]:
            x, y, w, h = p["rect"]
            tpl = f" 模板:{p['template']}" if p["template"] else ""
            out.append(f"- `{p['task']}` {p['action']} roi[{x},{y},{w},{h}] "
                       f"{p['cond']}{tpl}")
        if len(items) > max_per_cell:
            out.append(f"- …共 {len(items)} 个（省略 {len(items) - max_per_cell}）")
        out.append("")
    return "\n".join(out)


def query_region(points: list[dict], x: int, y: int, radius: int = 80) -> str:
    """查询某个坐标被谁点击/识别。"""
    hits = [p for p in points
            if (p["rect"][0] - radius <= x <= p["rect"][0] + p["rect"][2] + radius
                and p["rect"][1] - radius <= y <= p["rect"][1] + p["rect"][3] + radius)]
    hits.sort(key=lambda p: abs(p["rect"][0] + p["rect"][2] // 2 - x)
              + abs(p["rect"][1] + p["rect"][3] // 2 - y))
    out = [f"坐标 ({x},{y}) ±{radius}px 命中 {len(hits)} 个点：", ""]
    for p in hits[:15]:
        rx, ry, rw, rh = p["rect"]
        out.append(f"- `{p['task']}` roi[{rx},{ry},{rw},{rh}] {p['cond']}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="MAA UI 布局逆向工具")
    ap.add_argument("--grid", type=int, default=8, help="网格数（默认 8×8）")
    ap.add_argument("--region", help="查询坐标 x,y（如 877,532）")
    ap.add_argument("--top", type=int, default=0, help="只列点击最密集的 N 个区域")
    ap.add_argument("--source", help="tasks.json 路径")
    ap.add_argument("--out", help="输出到文件")
    args = ap.parse_args()

    tasks = load_tasks(args.source)
    points = collect_points(tasks)
    print(f"共收集 {len(points)} 个点击/识别点", file=sys.stderr)

    if args.region:
        x, y = (int(v) for v in args.region.split(","))
        print(query_region(points, x, y))
        return

    text = render_grid(points, args.grid, args.grid, 6)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
