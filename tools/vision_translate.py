#!/usr/bin/env python3
"""图片转译工具 — 把 MAA 模板图/截图转成文本描述，供无视觉的 LLM（DeepSeek）分析。

用法:
    python tools/vision_translate.py <图片路径> [--no-ocr] [--json]
    python tools/vision_translate.py --dir <目录> [--no-ocr]

转译四通道:
    ① OCR 文字（复用 ocr_tool 的 PaddleOCR 管线，缺依赖自动降级）
    ② 文件名/路径语义（路径与目录即分类）
    ③ 像素特征（尺寸/主色/金色占比/亮度）
    ④ 任务反向引用（tasks.json 里哪些任务用这张图、怎么用）

典型:
    python tools/vision_translate.py services/maa/source/resource/template/Battle/UsePrts/UsePrts-AnnihilationSuccess.png
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

# ── 可选依赖：OCR 与像素特征，缺失自动降级 ──
try:
    import cv2
    import numpy as np
    import onnxruntime as ort

    _HAS_VISION = True
except Exception:
    cv2 = np = ort = None
    _HAS_VISION = False

try:
    import ocr_tool

    _HAS_OCR = True
except Exception:
    ocr_tool = None
    _HAS_OCR = False

# 金色高亮判定（与 stage_probe 同阈值）
_GOLD = lambda b, r, g: (b < 110) & (r > 150) & (g > 90)  # noqa: E731

_ocr_sessions = {}


def _get_ocr():
    """懒加载 OCR 会话（两个 ONNX 模型，~13MB）。"""
    if not _HAS_OCR or not _HAS_VISION:
        return None
    if not _ocr_sessions:
        try:
            det = ort.InferenceSession(ocr_tool.DET_ONNX, providers=["CPUExecutionProvider"])
            rec = ort.InferenceSession(ocr_tool.REC_ONNX, providers=["CPUExecutionProvider"])
            keys = ocr_tool.load_keys(ocr_tool.KEYS_TXT)
            _ocr_sessions.update(det=det, rec=rec, keys=keys)
        except Exception as e:
            print(f"[OCR 加载失败，降级: {e}]", file=sys.stderr)
            return None
    return _ocr_sessions


def _ocr_text(img) -> str:
    """OCR 识别图片文字，返回 "文字(置信度)" 列表。"""
    s = _get_ocr()
    if s is None:
        return ""
    try:
        regions = ocr_tool.det_text_regions(img, s["det"])
        out = []
        for reg in regions:
            text, score = ocr_tool.rec_text(img, reg, s["rec"], s["keys"])
            if text and score > 0.3:
                out.append(f"{text}({score:.2f})")
        return ", ".join(out)
    except Exception as e:
        return f"[OCR 失败: {e}]"


def _pixel_features(img) -> dict:
    """像素特征：主色 / 金色占比 / 亮度。"""
    b = img[:, :, 2].astype(int)
    r = img[:, :, 0].astype(int)
    g = img[:, :, 1].astype(int)
    gold = float(_GOLD(b, r, g).mean())
    # 主色：量化到 32 级
    q = (img // 32 * 32).reshape(-1, 3)
    total = len(q)
    top = []
    for (cr, cg, cb), c in Counter(map(tuple, q)).most_common(3):
        top.append(f"#{cr:02X}{cg:02X}{cb:02X} {c / total:.0%}")
    return {
        "size": f"{img.shape[1]}x{img.shape[0]}",
        "dominant_colors": ", ".join(top),
        "gold_ratio": round(gold, 3),
        "mean_brightness": int(img.mean()),
    }


def _task_refs(tasks: dict, filename: str) -> list[str]:
    """反向引用：哪些任务用这张图（template 字段 / 缺省 任务名.png）。"""
    out = []
    for name, t in tasks.items():
        tpl = t.get("template")
        hit = False
        if isinstance(tpl, str) and tpl == filename:
            hit = True
        elif isinstance(tpl, list) and filename in tpl:
            hit = True
        elif not tpl and (name + ".png" == filename or name == filename):
            hit = True
        if hit:
            roi = t.get("roi") or ""
            nxt = (t.get("next") or [])
            nxt = ", ".join(str(x) for x in nxt[:3]) if isinstance(nxt, list) else str(nxt)
            out.append(
                f"{name} [{t.get('algorithm', 'MatchTemplate')} | "
                f"{t.get('action', 'DoNothing')} | roi {roi} | next: {nxt}]"
            )
    return out


def translate(img_path: Path, tasks: dict, do_ocr: bool = True) -> dict:
    """转译单张图片 → dict。"""

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(PROJ))
        except ValueError:
            return str(p)

    result = {
        "file": _rel(img_path),
        "directory": _rel(img_path.parent),
        "filename": img_path.name,
    }
    img = cv2.imread(str(img_path)) if _HAS_VISION else None
    if img is None:
        result["note"] = "无法读取图片（无 cv2 或文件损坏）"
        result["refs"] = _task_refs(tasks, img_path.name)
        return result
    result.update(_pixel_features(img))
    if do_ocr:
        text = _ocr_text(img)
        result["ocr_text"] = text if text else "（无文字）"
    result["refs"] = _task_refs(tasks, img_path.name)
    return result


def to_markdown(r: dict) -> str:
    lines = [f"### {r['filename']}"]
    lines.append(f"- 路径: `{r['file']}`")
    if "size" in r:
        lines.append(f"- 尺寸: {r['size']} | 主色: {r['dominant_colors']} "
                     f"| 金色占比: {r['gold_ratio']} | 亮度: {r['mean_brightness']}")
    if "ocr_text" in r:
        lines.append(f"- OCR 文字: {r['ocr_text']}")
    if "note" in r:
        lines.append(f"- 注意: {r['note']}")
    if r.get("refs"):
        lines.append(f"- 被任务引用 ({len(r['refs'])}):")
        for ref in r["refs"]:
            lines.append(f"  - {ref}")
    else:
        lines.append("- 未被任务直接引用（可能是动态模板）")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="MAA 图片转译工具（喂给无视觉 LLM）")
    ap.add_argument("image", nargs="?", help="图片路径")
    ap.add_argument("--dir", help="批量转译目录（*.png）")
    ap.add_argument("--no-ocr", action="store_true", help="跳过 OCR（模型缺失时）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--source", help="tasks.json 路径（反向引用用）")
    args = ap.parse_args()

    from task_graph import load_tasks

    tasks = load_tasks(args.source)

    if args.dir:
        files = sorted(Path(args.dir).glob("*.png"))
        if not files:
            sys.exit(f"目录 {args.dir} 没有 png")
    elif args.image:
        files = [Path(args.image)]
    else:
        sys.exit("需要图片路径或 --dir")

    results = [translate(p, tasks, do_ocr=not args.no_ocr) for p in files]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("\n".join(to_markdown(r) for r in results))


if __name__ == "__main__":
    main()
