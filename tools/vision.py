#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部视觉模型通道 —— **不必用 OCR**（用户 2026-09-24 提醒）。

项目里本来就配好了外部图像识别模型（modlens：providers.anthropic → https://opencode.ai/zen/，
模型 qwen3.8-max，key 已配）。凡是"看不出/认不准"的环节，直接问它，而不是我手写模板或调 OCR 阈值：
    · 这张卡是哪个活动？          → 认卡（替代"我读图认名"+模板库）
    · 面板报酬里有没有源石徽章？   → 唯一必须准的那一步（可与像素判据交叉验证）
    · 「进入活动」按钮在哪？       → 返回大致坐标，直接点（替代硬编码坐标）
    · 图上写着哪些关卡代号？       → 运行时发现关卡（替代 data/_stage_table.json）

用法:
    python tools/vision.py --ask "这张卡是哪个活动？"                 # 用当前屏
    python tools/vision.py --ask "进入活动按钮大约在什么位置？" --shot runtime/_x.png
    python tools/vision.py --json --ask "..."                        # 机器可读（给服务/队列用）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
SKILL = pathlib.Path.home() / ".agents" / "skills" / "modlens" / "scripts" / "run.ps1"


def grab(path: pathlib.Path) -> pathlib.Path:
    urllib.request.urlopen(f"http://127.0.0.1:8765/shot?path={path}", timeout=25).read()
    return path


def ask(image: pathlib.Path, question: str, timeout_ms: int = 120000) -> str:
    """把图交给外部视觉模型，返回它的回答（文本）。失败时返回空串。"""
    if SKILL.exists():
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(SKILL),
               "-i", str(image), "--prompt", question, "--timeout", str(timeout_ms)]
    else:                                        # 兜底：直接 npx 调
        cmd = ["npx", "--yes", "--package", "@liustack/modlens@3.12.1", "modlens",
               "-i", str(image), "--prompt", question, "--timeout", str(timeout_ms)]
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout_ms // 1000 + 60)
    out = (r.stdout or "").strip()
    if not out:
        return ""
    try:                                          # modlens 输出 JSON；取摘要/全文
        d = json.loads(out[out.find("{"):]) if "{" in out else {}
        res = d.get("result") or {}
        # **要 JSON 就直接给原始文本**（2026-09-24）：调用方常要坐标/结构化结果，
        # 而 result.summary 只是模型的自然语言总结、会把 JSON 丢掉（实测：问关卡坐标，
        # 摘要里只剩"八个节点 EP-EX-1 到 EP-EX-8"，坐标没了）。
        raw = res.get("text") or res.get("output") or res.get("raw") or res.get("content") or ""
        if "JSON" in question.upper() or "json" in question or "{" in str(raw):
            for cand in (raw, out):
                if "{" in str(cand):
                    return str(cand)
        return res.get("summary") or (res.get("ocr") or {}).get("full_text") or out[-400:]
    except Exception:
        return out[-400:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", required=True)
    ap.add_argument("--shot", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    img = pathlib.Path(a.shot) if a.shot else ROOT / "runtime" / "_vision.png"
    if not a.shot:
        grab(img)
    ans = ask(img, a.ask)
    if a.json:
        print(json.dumps({"ok": bool(ans), "answer": ans, "shot": str(img)}, ensure_ascii=False))
    else:
        print(ans or "（视觉模型没有返回内容）")
    return 0 if ans else 1


if __name__ == "__main__":
    raise SystemExit(main())
