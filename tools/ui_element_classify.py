#!/usr/bin/env python3
"""UI 元素类型识别 — 判断一个区域里是「按钮/文字/图标/状态标记/空背景」。

回答「MAA 点的那个位置是什么东西」：
    ① 视觉特征规则（可解释，零训练）: 内容密度/边缘密度/金色占比/宽高比/主色数
    ② 模板库先验: image_recognizer 认出最像哪个模板 → 目录即类型标注

用法:
    python tools/ui_element_classify.py <图片路径> [--json]
    python tools/ui_element_classify.py --batch <目录>   # 批量评估 vs 目录先验
"""
from __future__ import annotations
import argparse
import json
import sys
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

# 模板目录 → 元素类型先验
_DIR_TYPE = {
    "StartButton": "按钮",
    "UsePrts": "按钮/状态",
    "Award": "按钮",
    "Recruit": "按钮",
    "Battle": "按钮/状态",
    "StageNavigation": "导航元素",
    "items": "物品图标",
    "InfrastPic": "基建元素",
    "UiTheme": "主题图标",
    "Popup": "弹窗",
    "Roguelike": "肉鸽元素",
    "RA": "生息元素",
    "MiniGame": "小游戏元素",
    "Copilot": "作业元素",
    "empty": "空占位",
}


def _features(img: np.ndarray) -> dict:
    """提取可解释视觉特征（模板图带 alpha 通道时按 alpha 区分前景）。"""
    if img.ndim == 3 and img.shape[2] == 4:
        # 透明背景模板图: 前景 = alpha > 0
        alpha = img[:, :, 3] > 0
        bgr = img[:, :, :3]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        density = float(alpha.mean())
        edges = cv2.Canny(gray, 60, 160)
        fg = alpha[edges > 0]
        edge_density = float(fg.mean()) if alpha.any() else 0.0
        sel = alpha.reshape(-1)
        b = bgr[:, :, 2].reshape(-1)[sel].astype(int)
        r = bgr[:, :, 0].reshape(-1)[sel].astype(int)
        g = bgr[:, :, 1].reshape(-1)[sel].astype(int)
        gold = float(((b < 110) & (r > 150) & (g > 90)).mean()) if sel.any() else 0.0
        q = (bgr // 32 * 32).reshape(-1, 3)[sel]
        from collections import Counter
        total = max(1, len(q))
        n_colors = sum(1 for _, c in Counter(map(tuple, q)).items() if c / total > 0.02)
    else:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        thr, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        density = float((gray > thr).mean())
        edges = cv2.Canny(gray, 60, 160)
        edge_density = float((edges > 0).mean())
        b = img[:, :, 2].astype(int); r = img[:, :, 0].astype(int); g = img[:, :, 1].astype(int)
        gold = float(((b < 110) & (r > 150) & (g > 90)).mean())
        q = (img // 32 * 32).reshape(-1, 3)
        from collections import Counter
        total = len(q)
        n_colors = sum(1 for _, c in Counter(map(tuple, q)).items() if c / total > 0.02)
    return {
        "w": w, "h": h, "aspect": round(w / max(1, h), 2),
        "content_density": round(density, 3),
        "edge_density": round(edge_density, 3),
        "gold_ratio": round(gold, 3),
        "n_colors": n_colors,
    }


def classify_features(f: dict) -> tuple[str, list[str]]:
    """规则引擎: 特征 → 元素类型 + 证据链。"""
    ev = []
    if f["content_density"] < 0.05:
        ev.append(f"内容密度 {f['content_density']} < 0.05 → 近乎空白")
        return "空背景", ev
    if f["gold_ratio"] > 0.30:
        ev.append(f"金色占比 {f['gold_ratio']} > 0.30 → 高亮/激活态")
        return "高亮按钮/激活状态", ev
    if f["aspect"] > 4.0 and f["content_density"] < 0.30:
        ev.append(f"宽高比 {f['aspect']} > 4 且密度 {f['content_density']} 适中 → 长条文字/进度条")
        return "文字条/进度条", ev
    if f["content_density"] > 0.25 and f["edge_density"] > 0.15:
        ev.append(f"密度 {f['content_density']} > 0.25 且边缘 {f['edge_density']} > 0.15 → 有内容有边框")
        return "按钮/图标按钮", ev
    if f["edge_density"] > 0.15:
        ev.append(f"边缘密度 {f['edge_density']} > 0.15 但内容稀疏 → 线条图标/符号")
        return "线条图标/符号", ev
    if f["content_density"] > 0.12:
        ev.append(f"密度 {f['content_density']} 中等 → 图标/符号")
        return "图标/符号", ev
    ev.append(f"密度 {f['content_density']} 低 → 稀疏文字/标记")
    return "稀疏文字/标记", ev


def template_prior(img_path: Path) -> tuple[str, float] | None:
    """模板库先验: 认出最像的模板 → 目录类型。"""
    try:
        from image_recognizer import recognize
        rs = recognize(str(img_path), top_k=1, min_score=0.5)
        if rs:
            r = rs[0]
            for d, t in _DIR_TYPE.items():
                if d in r["path"]:
                    return f"{t}（模板 {r['path']}，相似 {r['score']}）", r["score"]
            return f"其他（模板 {r['path']}，相似 {r['score']}）", r["score"]
    except Exception:
        pass
    return None


def classify(img_path: str) -> dict:
    if not _HAS_CV:
        sys.exit("需要 cv2/numpy")
    img = cv2.imread(img_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        sys.exit(f"无法读取: {img_path}")
    if img.ndim == 2:  # 灰度 PNG
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    f = _features(img)
    rule_verdict, evidence = classify_features(f)
    prior = template_prior(Path(img_path))
    # 融合: 模板库命中(≥0.7) → 用人工标注的先验; 否则用规则
    final = rule_verdict
    if prior and prior[1] >= 0.7:
        t = prior[0].split("（")[0]
        final = f"{t}（模板先验）"
        evidence = evidence + [f"模板库命中 {prior[1]} → 人工标注类型覆盖规则"]
    return {"path": img_path, "verdict": final, "features": f,
            "evidence": evidence, "prior": prior, "rule_verdict": rule_verdict}


def main() -> None:
    ap = argparse.ArgumentParser(description="UI 元素类型识别")
    ap.add_argument("image", nargs="?", help="图片路径")
    ap.add_argument("--batch", help="批量评估目录（报告 vs 目录先验一致率）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.batch:
        from collections import Counter
        files = sorted(Path(args.batch).rglob("*.png"))
        total, ok = 0, 0
        by_dir: Counter = Counter()
        for f in files:
            verdict = classify(str(f))["verdict"]
            d = "unknown"
            for k in _DIR_TYPE:
                if k in str(f):
                    d = k
                    break
            by_dir[d] += 1
            # 粗略一致率: 按钮类目录判出"按钮/高亮"
            is_btn_dir = d in ("StartButton", "UsePrts", "Award", "Recruit")
            is_btn_verdict = "按钮" in verdict or "高亮" in verdict
            total += 1
            if is_btn_dir and is_btn_verdict or not is_btn_dir and not is_btn_verdict:
                ok += 1
        print(f"批量评估 {total} 张: 规则 vs 目录先验一致率 {ok}/{total} = {ok / max(1, total):.0%}")
        print("目录分布:", dict(by_dir))
        return

    r = classify(args.image)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    print(f"判定: **{r['verdict']}**")
    print("\n特征:", ", ".join(f"{k}={v}" for k, v in r["features"].items()))
    print("\n证据链:")
    for e in r["evidence"]:
        print(f"  - {e}")
    if r["prior"]:
        print(f"\n模板库先验: {r['prior'][0]}")


if __name__ == "__main__":
    main()
