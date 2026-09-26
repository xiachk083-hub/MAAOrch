#!/usr/bin/env python3
"""干员「专精 / 模组」图像识别 —— 用 MAA 自带模板读界面上的标记，不依赖任何登录。

为什么这么做（2026-09-15 用户）:
    · MAA 的「干员识别」(OperBox) 只回 精英化 / 等级 / 潜能 —— 读不到专精与模组；
    · 森空岛官方接口拿不全（代肝客户号不该碰登录态、B 服/无森空岛的号也没有）；
    · 但界面上的这两项**本身就是小图标**，MAA 自己的模板就是它们，模板匹配即可，
      单帧全屏匹配 ~150ms（12 个模板），不用 OCR 猜数字。

模板语义（实测 montage 确认，模板在 MAA resource/template/Battle/Formation/BattleQuickFormation/）:
    SkillLevel-07            → 数字 7            = 技能 7 级（未专精）
    SkillLevel-08 / 09 / 10  → 1 / 2 / 3 个亮点  = 专一 / 专二 / 专三
    SkillLevel-Base          → 闪电（技能图标本体，未显示等级时）
    Module-0                 → 「ORIGINAL」框     = 未装模组
    Module-1 / 2-A / 3 / 4   → 模组形状 X / Y / α / Δ
    Elite1 / Elite2          → 精英化标记

用法:
    python tools/mastery_scan.py --image logs/agent/sr1_squad.png     # 离线：拿现成截图校准
    python tools/mastery_scan.py --emu 1                              # 实时：adb 抓一帧
    python tools/mastery_scan.py --emu 1 --watch 120                  # 盯屏 2 分钟，攒证据
    python tools/mastery_scan.py --emu 1 --ocr                        # 顺带 OCR 附近的名字/等级

产出: data/mastery_scan/<tag>.json + <tag>_annotated.png（标注图，人眼一秒核对）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

import cv2
import numpy as np

PROJ = pathlib.Path(__file__).resolve().parent.parent
TPL_DIR = (PROJ / "services" / "maa" / "source" / "resource" / "template"
           / "Battle" / "Formation" / "BattleQuickFormation")
OUT_DIR = PROJ / "data" / "mastery_scan"

# 模板 → (含义, 匹配阈值)。点状模板(08/09/10)小且容易误报，阈值给高些。
TEMPLATES: dict[str, tuple[str, float]] = {
    "BattleQuickFormationSkillLevel-07": ("技能7", 0.90),
    "BattleQuickFormationSkillLevel-08": ("专一", 0.90),
    "BattleQuickFormationSkillLevel-09": ("专二", 0.90),
    "BattleQuickFormationSkillLevel-10": ("专三", 0.90),
    "BattleQuickFormationSkillLevel-Base": ("技能图标", 0.88),
    "BattleQuickFormationModule-0": ("无模组(ORIGINAL)", 0.82),
    "BattleQuickFormationModule-1": ("模组X", 0.85),
    "BattleQuickFormationModule-2-A": ("模组Y", 0.85),
    "BattleQuickFormationModule-3": ("模组α", 0.85),
    "BattleQuickFormationModule-4": ("模组Δ", 0.85),
    "BattleQuickFormation-Elite1": ("精一", 0.85),
    "BattleQuickFormation-Elite2": ("精二", 0.85),
}


def load_templates() -> dict[str, tuple[str, float, np.ndarray]]:
    out = {}
    for name, (label, thr) in TEMPLATES.items():
        p = TPL_DIR / f"{name}.png"
        im = cv2.imread(str(p))
        if im is None:
            print(f"! 模板缺失：{p}", file=sys.stderr)
            continue
        out[name] = (label, thr, im)
    return out


def match_all(img: np.ndarray, tpl: np.ndarray, thr: float, nms: int = 10) -> list[tuple[int, int, int, int, float]]:
    """全屏模板匹配 + 贪心 NMS，返回 [(x, y, w, h, score)]"""
    th, tw = tpl.shape[:2]
    if th > img.shape[0] or tw > img.shape[1]:
        return []
    r = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(r >= thr)
    cand = sorted(zip(ys.tolist(), xs.tolist(), r[ys, xs].tolist()), key=lambda p: -p[2])
    keep: list[tuple[int, int, int, int, float]] = []
    for y, x, v in cand:
        if all(abs(x - kx) > nms or abs(y - ky) > nms for kx, ky, _, _, _ in keep):
            keep.append((x, y, tw, th, round(float(v), 3)))
    return keep


def scan_frame(img: np.ndarray, tpls: dict, extra_thr: float = 0.0) -> list[dict]:
    marks: list[dict] = []
    for name, (label, thr, tpl) in tpls.items():
        for x, y, w, h, s in match_all(img, tpl, max(thr, extra_thr) if extra_thr else thr):
            marks.append({"tpl": name.replace("BattleQuickFormation", ""), "label": label,
                          "conf": s, "xywh": [x, y, w, h]})
    marks.sort(key=lambda m: (m["xywh"][1], m["xywh"][0]))
    return marks


def ocr_lines(img: np.ndarray) -> list[tuple[str, list[int]]]:
    """整帧 OCR，返回 [(文字, [x,y,w,h])]，用于配对干员名/技能名"""
    sys.path.insert(0, str(PROJ))
    try:
        import ocr_tool
        import onnxruntime as ort
    except Exception as e:
        print(f"! OCR 不可用：{e}", file=sys.stderr)
        return []
    det = ort.InferenceSession(ocr_tool.DET_ONNX, providers=["CPUExecutionProvider"])
    rec = ort.InferenceSession(ocr_tool.REC_ONNX, providers=["CPUExecutionProvider"])
    keys = ocr_tool.load_keys(ocr_tool.KEYS_TXT)
    out: list[tuple[str, list[int]]] = []
    for reg in ocr_tool.det_text_regions(img, det):
        try:
            t = ocr_tool.rec_text(img, reg, rec, keys)
        except Exception:
            continue
        if t and t[0].strip():
            out.append((t[0].strip(), list(reg)))
    return out


def read_panel(img: np.ndarray, marks: list[dict], texts: list[tuple[str, list[int]]]) -> dict:
    """读左侧详情面板：干员名 + 每个技能的等级/专精标记 + 模组。

    2026-09-15 实机定位（选人页，1280x720）：
      · 卡片上的技能标记不可靠；**选中干员的左侧面板**每个技能都有一枚标记：
        技能图标（闪电，x≈59）在上，等级标记（数字7 / 专一专二专三 亮点，x≈9）在其下方 ~60px
      · 干员名在面板顶部（中文名在英文名下面），OCR 时优先取含中日文的行
    """
    def cjk(t: str) -> bool:
        return any('一' <= c <= '鿿' for c in t)

    # 干员名固定在面板顶部（y<140）；技能描述区不能当名字（2026-09-15 实测踩过）
    head = [(t, b) for t, b in texts if b[0] < 230 and b[1] < 140 and 20 < b[3] < 60]
    head.sort(key=lambda tb: (0 if cjk(tb[0]) else 1, -tb[1][3]))               # 中文优先，其次字号大
    name = head[0][0] if head else ""

    panel_marks = [m for m in marks if m["xywh"][0] < 230 and m["xywh"][2] < 60]
    icons = [m for m in panel_marks if m["label"] == "技能图标"]
    levels = [m for m in panel_marks if m["label"] in ("技能7", "专一", "专二", "专三")]
    icons.sort(key=lambda m: m["xywh"][1])
    out_skills = []
    for i, ic in enumerate(icons):
        ix, iy = ic["xywh"][0], ic["xywh"][1]
        best = None
        for lv in levels:
            lx, ly = lv["xywh"][0], lv["xywh"][1]
            if lx <= ix + 10 and -10 <= ly - iy <= 100:      # 图标下方、同一技能块内
                if best is None or abs(ly - iy) < abs(best["xywh"][1] - iy):
                    best = lv
        out_skills.append({"第几个技能": i + 1,
                           "等级标记": best["label"] if best else "（未检出，说明未到 7 级/未专精）",
                           "conf": best["conf"] if best else None})
    return {"op": name,
            "skills": out_skills,
            "module_marks": [m["label"] for m in panel_marks if "模组" in m["label"]],
            "panel_marks_all": [(m["label"], m["xywh"][0], m["xywh"][1], m["conf"]) for m in panel_marks]}


def annotate(img: np.ndarray, marks: list[dict]) -> np.ndarray:
    out = img.copy()
    for m in marks:
        x, y, w, h = m["xywh"]
        cv2.rectangle(out, (x - 2, y - 2), (x + w + 2, y + h + 2), (0, 255, 255), 1)
        cv2.putText(out, f"{m['label']} {m['conf']:.2f}", (max(0, x - 40), max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    cv2.putText(out, f"marks={len(marks)}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    return out


def ocr_names(img: np.ndarray, marks: list[dict]) -> list[dict]:
    """给每个标记补一个「附近的名字/等级」（可选，慢 0.5s/处）"""
    sys.path.insert(0, str(PROJ))
    try:
        import ocr_tool  # 仓库根的本地 PaddleOCR 封装
        import onnxruntime as ort
    except Exception as e:
        print(f"! OCR 不可用（跳过名字）：{e}", file=sys.stderr)
        return marks
    try:
        det = ort.InferenceSession(ocr_tool.DET_ONNX, providers=["CPUExecutionProvider"])
        rec = ort.InferenceSession(ocr_tool.REC_ONNX, providers=["CPUExecutionProvider"])
        keys = ocr_tool.load_keys(ocr_tool.KEYS_TXT)
    except Exception as e:
        print(f"! OCR 模型加载失败（跳过名字）：{e}", file=sys.stderr)
        return marks
    seen = set()
    for m in marks:
        x, y, w, h = m["xywh"]
        # 名字通常在标记下方 / 等级在标记上方，取一块包含卡片下半部的区域
        y1, y2 = max(0, y - 40), min(img.shape[0], y + 90)
        x1, x2 = max(0, x - 120), min(img.shape[1], x + 120)
        key = (y1 // 40, x1 // 120)
        if key in seen:
            continue
        seen.add(key)
        try:
            regs = ocr_tool.det_text_regions(img[y1:y2, x1:x2], det)
            texts = []
            for reg in regs[:4]:
                t = ocr_tool.rec_text(img[y1:y2, x1:x2], reg, rec, keys)
                if t and t[0].strip():
                    texts.append(t[0].strip())
            m["near_text"] = texts
        except Exception:
            pass
    return marks


def capture_frame(emu: str) -> np.ndarray | None:
    sys.path.insert(0, str(PROJ))
    from services.ai_agent.percept import AdbScreen
    sc = AdbScreen(emu)
    if not sc.ensure_connected():
        print(f"! 模拟器 #{emu} 没连上 adb", file=sys.stderr)
        return None
    return sc.capture()


def main() -> None:
    ap = argparse.ArgumentParser(description="干员专精/模组图像识别")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=pathlib.Path, help="离线截图路径")
    src.add_argument("--emu", help="模拟器索引（adb 实时抓帧，只读不操作）")
    ap.add_argument("--watch", type=float, default=0, help="盯屏秒数（配合 --emu）")
    ap.add_argument("--interval", type=float, default=2.0, help="盯屏抓帧间隔秒")
    ap.add_argument("--thr", type=float, default=0.0, help="统一覆盖阈值（默认按模板各自阈值）")
    ap.add_argument("--ocr", action="store_true", help="顺带 OCR 标记附近的名字/等级")
    ap.add_argument("--panel", action="store_true",
                    help="面板模式：读当前选中干员的「每个技能等级/专精 + 模组」（点一个读一个）")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    tpls = load_templates()
    print(f"模板 {len(tpls)} 个 ← {TPL_DIR}")

    frames: list[tuple[str, np.ndarray]] = []
    if args.image:
        im = cv2.imread(str(args.image))
        if im is None:
            sys.exit(f"读不了图片：{args.image}")
        frames.append((args.image.stem, im))
    else:
        if args.watch > 0:
            t0 = time.time()
            while time.time() - t0 < args.watch:
                f = capture_frame(args.emu)
                if f is not None:
                    frames.append((time.strftime("%H%M%S"), f))
                time.sleep(args.interval)
        else:
            f = capture_frame(args.emu)
            if f is None:
                sys.exit(1)
            frames.append((time.strftime("%H%M%S"), f))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    for tag, img in frames:
        marks = scan_frame(img, tpls, args.thr)
        panel_read = None
        if args.panel:
            lines = ocr_lines(img)
            panel_read = read_panel(img, marks, lines)
            print(f"    面板：{panel_read['op']} | "
                  + "；".join(f"技能{m['第几个技能']}:{m['等级标记']}" for m in panel_read["skills"])
                  + (f" | 模组 {panel_read['module_marks']}" if panel_read["module_marks"] else " | 模组标记未检出"))
        if args.ocr and marks and not args.panel:
            marks = ocr_names(img, marks)
        vis = annotate(img, marks)
        p_vis = OUT_DIR / f"{tag}_annotated.png"
        cv2.imwrite(str(p_vis), vis)
        kinds = {}
        for m in marks:
            kinds[m["label"]] = kinds.get(m["label"], 0) + 1
        print(f"  {tag}: {img.shape[1]}x{img.shape[0]} → {len(marks)} 个标记 {kinds}｜{p_vis.name}")
        report.append({"tag": tag, "size": [img.shape[1], img.shape[0]], "marks": marks,
                       "panel": panel_read})

    out = args.out or (OUT_DIR / f"scan_{time.strftime('%m%d_%H%M%S')}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果：{out}")


if __name__ == "__main__":
    main()
