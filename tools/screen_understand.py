#!/usr/bin/env python3
"""画面理解 — 给一张游戏截图，像人一样"看懂"当前是什么界面。

原理（基于 docs/MAA_STATE_MAP.md 的语义状态图）:
    对每个状态，验证它的识别信号（模板命中/OCR 文字/颜色特征）
    → 信号得分最高的状态 = 当前画面状态
    → 全部信号不命中 = 「未知界面」（人看到陌生画面会警惕，MAA 只会死磕）

用法:
    python tools/screen_understand.py <截图.png> [--no-ocr] [--json]
    python tools/screen_understand.py <截图.png> --verbose   # 显示全部状态得分

注意:
    - 截图分辨率 1280×720（其他分辨率自动缩放）
    - OCR 默认开启（PaddleOCR, ~3s/张）; --no-ocr 只靠模板+颜色（~0.3s）
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

from task_graph import load_tasks, resolve_task  # noqa: E402

TEMPLATE_DIR = (PROJ / "services" / "maa" / "source" / "resource" / "template")
if not TEMPLATE_DIR.exists():
    TEMPLATE_DIR = PROJ / "services" / "maa" / "instances" / "1" / "resource" / "template"

# 状态 → 验证器列表。
# ("模板", 任务名[, 阈值])    : 该任务 template 在 roi 内模板匹配
# ("主题", 名字, 任务名, 阈值) : UiTheme/*/{名字}.png 全部主题模板在任务 roi 内任一命中
# ("颜色金", (x,y,w,h), 阈值) : ROI 金色占比 ≥ 阈值（高亮按钮特征）
# ("OCR", 文字, roi)         : ROI 内 OCR 命中文字（--no-ocr 时跳过）
STATE_CHECKS = {
    "启动/登录页": [("模板", "GameStart", 0.6)],
    "加载中": [("模板", "LoadingIcon", 0.6)],
    "公告/弹窗清理": [("模板", "CloseAnno", 0.6)],
    "主界面": [("模板", "Fight", 0.5)],
    "终端页": [("主题", "Terminal", "TerminalDefault", 0.7)],
    "关卡选择页": [("OCR", ["EPISODE", "关卡"], (0, 0, 1280, 720))],
    "关卡详情页": [("模板", "UsePrts-Annihilation", 0.7),
                  ("模板", "UsePrts", 0.7),
                  ("模板", "UsePrtsSuccessCheck", 0.9),
                  ("颜色金任务", "StartButton1", 0.10),
                  ("金色块", 0.30)],
    "战斗中": [("模板", "PRTS1", 0.4)],
    "结算页": [("模板", "EndOfAction", 0.5)],
    "失败页": [("OCR", ["任务失败"], (36, 226, 482, 269))],
    "掉线弹窗": [("模板", "OfflineConfirm", 0.6)],
    "确认弹窗": [("模板", "ReturnConfirm", 0.6)],
    "剿灭列表页": [("模板", "Annihilation", 0.5),
                  ("OCR", ["剿灭", "炎国"], (0, 0, 1280, 720))],
    "基建页": [("模板", "InfrastEnteredFlag", 0.75)],
    "公招页": [("OCR", ["公开招募", "开始招"], (0, 100, 1280, 500))],
    "任务/领取页": [("模板", "ReceiveAward", 0.6)],
    "商店页": [("OCR", ["信用交易所", "凭证"], (0, 0, 1280, 200))],
    "抽卡页": [("OCR", ["寻访"], (0, 0, 1280, 720))],
}

_tasks_cache = None


def _tasks():
    global _tasks_cache
    if _tasks_cache is None:
        _tasks_cache = load_tasks(None)
    return _tasks_cache


def _template_check(img_gray: np.ndarray, task_name: str, thr: float) -> tuple[bool, float]:
    """任务 template 在其 roi 内匹配截图，返回 (命中, 得分)。"""
    resolved = resolve_task(_tasks(), task_name)
    if resolved is None:
        return False, 0.0
    definition, _ = resolved
    roi = definition.get("roi")
    if not roi or len(roi) != 4:
        # 无 roi 的任务跳过（全屏 OCR 类）
        return False, 0.0
    tpl = definition.get("template")
    if tpl is None:
        tpl = task_name + ".png"  # MAA 缺省规则: 模板 = 任务名.png
    if isinstance(tpl, list):
        tpl = tpl[0]
    if not tpl or tpl == "empty.png":
        return False, 0.0
    # MAA 自带 templThreshold 优先（它是调参结果），否则用调用方阈值
    tt = definition.get("templThreshold")
    if isinstance(tt, (int, float)):
        thr = float(tt)
    elif isinstance(tt, list) and tt:
        thr = float(tt[0])
    tpl_path = TEMPLATE_DIR / tpl
    if not tpl_path.exists():
        found = list(TEMPLATE_DIR.rglob(tpl))
        tpl_path = found[0] if found else None
    if tpl_path is None:
        return False, 0.0
    t = cv2.imread(str(tpl_path), cv2.IMREAD_GRAYSCALE)
    if t is None:
        return False, 0.0
    x0, y0, w, h = roi
    ih, iw = img_gray.shape
    x1, y1 = min(x0 + w, iw), min(y0 + h, ih)
    if x1 <= x0 or y1 <= y0:
        return False, 0.0
    reg = img_gray[y0:y1, x0:x1]
    th, tw = t.shape
    if th > reg.shape[0] or tw > reg.shape[1]:
        return False, 0.0
    res = cv2.matchTemplate(reg, t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, _ = cv2.minMaxLoc(res)
    return float(mx) >= thr, float(mx)


def _theme_check(img_gray: np.ndarray, theme: str, anchor_task: str, thr: float) -> tuple[bool, float]:
    """UiTheme/*/{theme}.png 全部主题模板在锚点任务 roi 内匹配，任一命中即可。"""
    resolved = resolve_task(_tasks(), anchor_task)
    if resolved is None:
        return False, 0.0
    roi = resolved[0].get("roi")
    # 锚点任务自带 templThreshold 优先（MAA 调参结果）
    tt = resolved[0].get("templThreshold")
    if isinstance(tt, (int, float)):
        thr = float(tt)
    elif isinstance(tt, list) and tt:
        thr = float(tt[0])
    if not roi or len(roi) != 4:
        return False, 0.0
    x0, y0, w, h = roi
    ih, iw = img_gray.shape
    x1, y1 = min(x0 + w, iw), min(y0 + h, ih)
    if x1 <= x0 or y1 <= y0:
        return False, 0.0
    reg = img_gray[y0:y1, x0:x1]
    best = 0.0
    for tpl in sorted((TEMPLATE_DIR / "UiTheme").glob(f"*/{theme}.png")):
        t = cv2.imread(str(tpl), cv2.IMREAD_GRAYSCALE)
        if t is None:
            continue
        th, tw = t.shape
        if th > reg.shape[0] or tw > reg.shape[1]:
            continue
        res = cv2.matchTemplate(reg, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, _ = cv2.minMaxLoc(res)
        if mx > best:
            best = float(mx)
    return best >= thr, best


def _gold_check(img: np.ndarray, roi: tuple, thr: float) -> tuple[bool, float]:
    x0, y0, w, h = roi
    reg = img[y0:y0 + h, x0:x0 + w]
    b = reg[:, :, 2].astype(int); r = reg[:, :, 0].astype(int); g = reg[:, :, 1].astype(int)
    ratio = float(((b < 110) & (r > 150) & (g > 90)).mean())
    return ratio >= thr, ratio


def _gold_block_check(img: np.ndarray, thr: float) -> tuple[bool, float]:
    """全图 80×40 块扫描金色密度——不依赖布局，不同关卡详情页按钮位置都找得到。
    详情页是深色背景；亮白页（活动列表等）金色卡片会误配，加亮度门控排除。"""
    if float(img.mean()) > 180:  # 亮白页排除
        return False, 0.0
    b = img[:, :, 2].astype(int); r = img[:, :, 0].astype(int); g = img[:, :, 1].astype(int)
    gold = (b < 110) & (r > 150) & (g > 90)
    best = 0.0
    for y in range(0, 720, 40):
        for x in range(0, 1280, 80):
            m = float(gold[y:y + 40, x:x + 80].mean())
            if m > best:
                best = m
    return best >= thr, best


def _gold_task_check(img: np.ndarray, task_name: str, thr: float) -> tuple[bool, float]:
    """按任务定义 roi 算金色占比（任务 roi 是 MAA 标注的准确布局）。"""
    resolved = resolve_task(_tasks(), task_name)
    if resolved is None:
        return False, 0.0
    roi = resolved[0].get("roi")
    if not roi or len(roi) != 4:
        return False, 0.0
    return _gold_check(img, tuple(roi), thr)


def _ocr_texts(img: np.ndarray) -> list[str]:
    try:
        sys.path.insert(0, str(PROJ))
        import ocr_tool
        import onnxruntime as ort

        det = ort.InferenceSession(ocr_tool.DET_ONNX, providers=["CPUExecutionProvider"])
        rec = ort.InferenceSession(ocr_tool.REC_ONNX, providers=["CPUExecutionProvider"])
        keys = ocr_tool.load_keys(ocr_tool.KEYS_TXT)
        out = []
        for reg in ocr_tool.det_text_regions(img, det):
            text, score = ocr_tool.rec_text(img, reg, rec, keys)
            if text and score > 0.3:
                out.append(text)
        return out
    except Exception as e:
        return [f"__OCR_FAIL__{e}"]


def understand(img_path: str, use_ocr: bool = True) -> dict:
    if not _HAS_CV:
        sys.exit("需要 cv2/numpy")
    img = cv2.imread(img_path)
    if img is None:
        sys.exit(f"无法读取: {img_path}")
    h, w = img.shape[:2]
    if (w, h) != (1280, 720):
        img = cv2.resize(img, (1280, 720))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ocr_texts = _ocr_texts(img) if use_ocr else []

    results = []
    for state, checks in STATE_CHECKS.items():
        score = 0.0
        evidence = []
        for check in checks:
            kind = check[0]
            if kind == "模板":
                hit, val = _template_check(gray, check[1], check[2])
                if hit:
                    score += 1.0
                evidence.append(f"{'✓' if hit else '✗'} 模板 {check[1]} 得分 {val:.2f}")
            elif kind == "主题":
                hit, val = _theme_check(gray, check[1], check[2], check[3])
                if hit:
                    score += 1.0
                evidence.append(f"{'✓' if hit else '✗'} 主题 {check[1]}×N 最高 {val:.2f}")
            elif kind == "颜色金":
                hit, val = _gold_check(img, check[1], check[2])
                if hit:
                    score += 0.8
                evidence.append(f"{'✓' if hit else '✗'} 金色占比 {val:.2f} (阈值 {check[2]})")
            elif kind == "颜色金任务":
                hit, val = _gold_task_check(img, check[1], check[2])
                if hit:
                    score += 0.8
                evidence.append(f"{'✓' if hit else '✗'} 金色占比(任务roi {check[1]}) {val:.2f}")
            elif kind == "金色块":
                hit, val = _gold_block_check(img, check[1])
                if hit:
                    score += 0.8
                evidence.append(f"{'✓' if hit else '✗'} 金色块最高密度 {val:.2f}")
            elif kind == "OCR":
                if use_ocr:
                    hit = any(any(t in t2 for t in check[1]) for t2 in ocr_texts)
                    if hit:
                        score += 1.0
                    evidence.append(f"{'✓' if hit else '✗'} OCR {check[1]}")
                else:
                    evidence.append("· OCR 跳过")
        if score > 0:
            results.append({"state": state, "score": round(score, 2),
                            "evidence": evidence})
    results.sort(key=lambda r: -r["score"])

    if not results:
        return {"verdict": "未知界面", "score": 0.0,
                "detail": "没有任何已知状态信号命中——建议记录截图人工确认，不要死磕",
                "candidates": [], "ocr_texts": ocr_texts[:10]}
    top = results[0]
    verdict = top["state"]
    detail = "；".join(top["evidence"][:4])
    # 详情页防误判：标志区（代理/开始行动/中央按钮）至少一个金色 >0.05
    # （暗色关卡列表的金色卡片会误触发金色块信号，但列表页三个标志区全暗）
    # 2026-08-12: 普通/磨难关卡按钮是暗色（非金色）→ 有 UsePrtsSuccess
    # 强信号时不再要求金色（否则磨难详情页被误判"非详情页"）。
    if verdict == "关卡详情页":
        zones = [(877, 532, 183, 123), (1010, 625, 260, 61), (960, 360, 160, 140)]
        zone_vals = [_gold_check(img, z, 0.05)[1] for z in zones]
        prts_hit, _prts_val = _template_check(gray, "UsePrtsSuccessCheck", 0.9)
        if max(zone_vals) < 0.05 and not prts_hit:
            verdict = "关卡列表/非详情页（标志区无金色按钮）"
            detail += f"；标志区金色 {[round(v, 3) for v in zone_vals]}"
    # 可刷性细分：详情页内看代理按钮状态（金色高亮=可刷；锁图标=不可刷）
    # 2026-08-12: 普通/磨难关卡全权委托按钮是暗色，金色信号失效 —
    # 权威信号改用 UsePrtsSuccess 模板（已激活图标，实测 0.998）vs
    # UnableToAgent2（锁定图标）。金色块仅作剿灭/活动关补充。
    if verdict == "关卡详情页":
        prts_hit, prts_val = _template_check(gray, "UsePrtsSuccessCheck", 0.9)
        locked_hit, locked_val = _template_check(gray, "UnableToAgent2", 0.6)
        if prts_hit:
            verdict = "关卡详情页 → 可刷（全权委托已激活 UsePrtsSuccess）"
            detail += f"；UsePrtsSuccess {prts_val:.2f}"
        elif locked_hit:
            verdict = "关卡详情页 → 不可刷（代理锁定 UnableToAgent2）"
            detail += f"；锁图标匹配 {locked_val:.2f}"
        else:
            gold_hit, gold_val = _gold_block_check(img, 0.30)
            if gold_hit:
                verdict = "关卡详情页 → 可刷（代理可用，金色按钮）"
                detail += f"；金色块 {gold_val:.2f}"
            else:
                verdict = "关卡详情页 → 可刷性待定（无金无锁，可能未解锁/理智不足）"
    return {"verdict": verdict, "score": top["score"],
            "detail": detail,
            "candidates": results[:4], "ocr_texts": ocr_texts[:10]}


def main() -> None:
    ap = argparse.ArgumentParser(description="画面理解（游戏截图 → 语义状态）")
    ap.add_argument("image", help="截图路径")
    ap.add_argument("--no-ocr", action="store_true", help="跳过 OCR（更快）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="显示全部状态得分")
    args = ap.parse_args()

    r = understand(args.image, use_ocr=not args.no_ocr)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    print(f"当前状态: **{r['verdict']}** (得分 {r['score']})")
    print(f"依据: {r['detail']}")
    if r.get("ocr_texts"):
        print(f"OCR 文字: {' | '.join(r['ocr_texts'][:8])}")
    if args.verbose and r.get("candidates"):
        print("\n候选状态:")
        for c in r["candidates"][1:]:
            print(f"  {c['state']} (得分 {c['score']}): {c['evidence'][0]}")


if __name__ == "__main__":
    main()
