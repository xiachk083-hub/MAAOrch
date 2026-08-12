#!/usr/bin/env python3
"""图片识别工具 — 用 MAA 模板图库「认出」任意图片是什么元素。

用法:
    python tools/image_recognizer.py <图片路径> [--top 5] [--min-score 0.5]
    python tools/image_recognizer.py <图片路径> --json

原理:
    ① 建库: 扫描 template/ 全部 png(2147 张), 存 尺寸/灰度缩略/主色/金色占比
    ② 识别: 尺寸先验过滤 → 多尺度 NCC(cv2.matchTemplate) → top-K 相似
    ③ 语义: 认出后告诉你 —— 模板路径(目录即分类) + 被哪些任务用(ROI/动作)

注意:
    - 输入应为「元素图」(ROI 裁剪/模板图), 不是全屏截图(模板库都是小元素)
    - 全屏截图先按 ROI 裁剪再识别 (参考 docs/MAA_UI_LAYOUT.md 的 ROI 表)
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

try:
    import cv2
    import numpy as np

    _HAS_CV = True
except Exception:
    cv2 = np = None
    _HAS_CV = False

try:
    from vision_translate import _task_refs
    from task_graph import load_tasks

    _HAS_TASKS = True
except Exception:
    _task_refs = load_tasks = None
    _HAS_TASKS = False

TEMPLATE_DIR = (PROJ / "services" / "maa" / "source" / "resource" / "template")
if not TEMPLATE_DIR.exists():
    TEMPLATE_DIR = (PROJ / "services" / "maa" / "instances" / "1" / "resource" / "template")

_index_cache: list[dict] | None = None
_tasks_cache: dict | None = None


def build_index(verbose: bool = True) -> list[dict]:
    """扫描模板库，每张存: 路径/尺寸/灰度缩略(64x64)/金色占比/主色。"""
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    if not _HAS_CV:
        sys.exit("需要 cv2/numpy: pip install opencv-python-headless numpy")
    entries = []
    files = sorted(TEMPLATE_DIR.rglob("*.png"))
    t0 = time.time()
    for f in files:
        try:
            img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            h, w = img.shape
            small = cv2.resize(img, (64, 64))
            color = cv2.imread(str(f))
            b = color[:, :, 2].astype(int); r = color[:, :, 0].astype(int); g = color[:, :, 1].astype(int)
            gold = float(((b < 110) & (r > 150) & (g > 90)).mean())
            entries.append({
                "path": str(f.relative_to(TEMPLATE_DIR)).replace("\\", "/"),
                "name": f.name,
                "w": w, "h": h,
                "small": small,  # 64x64 灰度
                "gold": gold,
            })
        except Exception:
            continue
    _index_cache = entries
    if verbose:
        print(f"模板库索引: {len(entries)} 张 ({time.time() - t0:.1f}s)", file=sys.stderr)
    return entries


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    """归一化互相关（-1~1）。"""
    a = a.astype(np.float32); b = b.astype(np.float32)
    a = (a - a.mean()) / (a.std() + 1e-6)
    b = (b - b.mean()) / (b.std() + 1e-6)
    return float((a * b).mean())


def recognize(img_path: str, top_k: int = 5, min_score: float = 0.5) -> list[dict]:
    """识别图片：返回 top-K [{name, path, score, gold, refs}]。"""
    if not _HAS_CV:
        sys.exit("需要 cv2/numpy")
    img = cv2.imread(img_path)
    if img is None:
        sys.exit(f"无法读取图片: {img_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ih, iw = gray.shape
    small = cv2.resize(gray, (64, 64))

    index = build_index()
    # ① 尺寸先验: 目标与模板尺寸比 0.5~2.5
    cands = [e for e in index
             if 0.5 <= iw / e["w"] <= 2.5 and 0.5 <= ih / e["h"] <= 2.5]
    # ② 多尺度 NCC: 目标缩放到模板尺寸对比缩略图, 取分数最高尺度
    results = []
    for e in cands:
        best = -1.0
        for scale in (0.8, 0.9, 1.0, 1.1, 1.2):
            tw, th = int(e["w"] * scale), int(e["h"] * scale)
            if tw < 8 or th < 8:
                continue
            r = cv2.resize(gray, (tw, th))
            r = cv2.resize(r, (64, 64))
            s = _ncc(r, e["small"])
            if s > best:
                best = s
        if best >= min_score:
            results.append({"name": e["name"], "path": e["path"],
                            "score": round(best, 3), "gold": e["gold"]})
    results.sort(key=lambda r: -r["score"])
    # ③ 语义: 反向引用任务
    if _HAS_TASKS and _task_refs:
        global _tasks_cache
        if _tasks_cache is None:
            _tasks_cache = load_tasks(None)
        for r in results:
            r["refs"] = _task_refs(_tasks_cache, r["name"])
    return results[:top_k]


def describe(r: dict) -> str:
    """单条结果的语义描述。"""
    lines = [f"- **{r['path']}** (相似度 {r['score']}, 金色占比 {r['gold']})"]
    if r.get("refs"):
        for ref in r["refs"][:3]:
            lines.append(f"  - 被 `{ref}` 使用")
    else:
        lines.append("  - 未被任务直接引用（可能被 @ 命名/路径引用）")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="用 MAA 模板图库认出图片")
    ap.add_argument("image", help="图片路径（元素图/ROI 裁剪）")
    ap.add_argument("--top", type=int, default=5, help="返回 top-K（默认 5）")
    ap.add_argument("--min-score", type=float, default=0.5, help="最低相似度（默认 0.5）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    results = recognize(args.image, args.top, args.min_score)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if not results:
        print(f"未认出（模板库里没有相似度 ≥ {args.min_score} 的元素）")
        print("提示: 输入应为元素图/ROI 裁剪; 全屏截图先用布局地图的 ROI 裁剪")
        return
    print(f"认出 {len(results)} 个候选：\n")
    for r in results:
        print(describe(r))


if __name__ == "__main__":
    main()
