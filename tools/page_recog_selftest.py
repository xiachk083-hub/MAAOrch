#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""page_recog_selftest.py —— **页面识别离线回归**（2026-09-24 独立审计产出）

为什么要有它：`page_of` 的两次事故（模板没登记进 specs、`>=` 把整屏模板整批跳过）
都是"**改完没人拿真实截图回归**"造成的，肉眼看不出来，直到线上判错才发现。
这个脚本把"拿 runtime/ 里的历史截图重放一遍"固化成一条命令。

它做四件事（**只读，不碰模拟器、不联网、不点任何东西**）：
  ① **清单体检**：`pages_load()` 实际加载到的模板 vs `data/templates/pages/*.png`，
     报出「文件存在但没登记」（静默失效的头号原因）与「登记了但文件不在」。
  ② **阈值体检**：对每张模板报"它在真实截图上的最高分/中位分"，看阈值离噪声有多远。
  ③ **全库重放**：对 `runtime/*.png` 里所有 1280x720 截图跑 `page_of`，出判定分布。
  ④ **回归断言**：把 `runtime/_pagerecog_baseline.json` 当基线，逐张比对——
     与基线不同的判定会被列出来（`--update` 才写回基线）。**判定变化必须解释**，
     这正是"复制了模板却忘了登记""改了阈值"这类改动该被拦住的地方。

用法:
    python tools/page_recog_selftest.py                 # 体检 + 重放 + 与基线比对
    python tools/page_recog_selftest.py --update        # 把当前结果写成新基线（确认无误后）
    python tools/page_recog_selftest.py --quiet         # 只打印结论与差异
退出码：有"没登记的模板"或"判定偏离基线" ⇒ 1；否则 0。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

import maaorch_service as S  # noqa: E402

BASELINE = ROOT / "runtime" / "_pagerecog_baseline.json"


def _specs_filenames(svc_path: pathlib.Path) -> set[str]:
    """用 AST 把 `pages_load()` 里 specs 列表的字符串字面量全部取出来（只要 .png 的）。

    比"按行 split('"')"稳：ROI 元组里的逗号/括号不会带偏解析。
    """
    import ast
    tree = ast.parse(svc_path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "pages_load":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and sub.value.endswith(".png"):
                    out.add(sub.value)
    return out


def load_templates() -> list[tuple[str, str, tuple | None]]:
    """从 `pages_load()` 反推 (页面名, 文件名, ROI)；拿不到就退回解析源码。"""
    out = []
    for name, t, search in S.pages_load():
        out.append((name, t, search))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--dir", default="runtime")
    a = ap.parse_args()

    tpl_dir = ROOT / "data" / "templates" / "pages"
    loaded = S.pages_load()
    names = [n for n, _t, _s in loaded]
    shapes = [(n, t.shape[1], t.shape[0]) for n, t, _s in loaded]

    print(f"① 模板清单：pages_load() 加载到 {len(loaded)} 条")
    for n, w, h in shapes:
        print(f"     {n:8s} {w}x{h}")

    # 文件系统 vs 加载结果：用 **AST 解析源码里 specs 的字符串字面量**（别用行切分——
    # ROI 元组里的逗号/括号会把朴素解析带偏，第一版就把 lobby.png 误报成"没登记"）。
    listed = _specs_filenames(ROOT / "tools" / "maaorch_service.py")

    on_disk = {p.name for p in tpl_dir.glob("*.png")}
    unregistered = sorted(on_disk - listed)
    missing = sorted(listed - on_disk)

    bad = 0
    if unregistered:
        print(f"  ⚠️ **磁盘上有、specs 里没登记**（等于不存在，静默失效）：{unregistered}")
        bad = 1
    if missing:
        print(f"  ⚠️ **specs 登记了、磁盘上没有**（会被 if not p.exists() 静默跳过）：{missing}")
        bad = 1
    if not unregistered and not missing:
        print("  ✅ 磁盘与 specs 一一对应，无静默失效")

    # ② 全库重放
    # ⚠️ 关键：`runtime/_*.png` 是**会被反复覆写的临时截图**（同一个文件名，不同时刻是不同画面）。
    #    所以基线必须**按内容**比对 —— 第一版只按文件名比，结果现场工具一截新图就报一堆
    #    "判定变化"，全是噪声、反而把真信号埋了。这里记 `sha` + `mean`：
    #      · sha 相同 ⇒ 同一张图，判定不同**才是真的回归**（必须解释）；
    #      · sha 不同 ⇒ 只是换了一张图，单独列成"内容已换"，不算失败。
    import hashlib
    files = sorted(glob.glob(str(ROOT / a.dir / "*.png")))
    results = {}
    dist: dict[str, int] = {}
    skipped = 0
    for f in files:
        img = cv2.imread(f)
        if img is None or img.shape[:2] != (720, 1280):
            skipped += 1
            continue
        r = S.page_of(img)
        rel = os.path.relpath(f, ROOT).replace("\\", "/")
        blob = pathlib.Path(f).read_bytes()
        results[rel] = {"page": r.get("page"), "score": r.get("page_score"),
                        "margin": r.get("page_margin"),
                        "sha": hashlib.sha256(blob).hexdigest()[:16],
                        "mean": round(float(img.mean()), 1)}
        dist[r.get("page")] = dist.get(r.get("page"), 0) + 1

    print(f"\n② 全库重放：{len(results)} 张 1280x720（跳过 {skipped} 张其他尺寸/读不到）")
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"     {k:8s} {v}")

    if not a.quiet:
        print("\n   判成「活动地图」的：")
        for rel, v in sorted(results.items(), key=lambda kv: -(kv[1]["score"] or 0)):
            if v["page"] == "活动地图":
                print(f"     {v['score']:.3f} m{(v['margin'] or 0):.3f}  {rel}")

    # ④ 与基线比对
    if a.update:
        BASELINE.write_text(json.dumps(results, ensure_ascii=False, indent=1, sort_keys=True),
                            encoding="utf-8")
        print(f"\n④ 已写入基线（{len(results)} 条）→ {os.path.relpath(BASELINE, ROOT)}")
        return bad

    if not BASELINE.exists():
        print(f"\n④ 还没有基线（{os.path.relpath(BASELINE, ROOT)}）；"
              f"确认上面结果无误后跑 --update 生成。")
        return bad

    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    real, churn, new = [], [], []
    for rel, cur in results.items():
        old = base.get(rel)
        if old is None:
            new.append((rel, cur))
        elif old.get("sha") == cur.get("sha"):
            if old.get("page") != cur.get("page"):
                real.append((rel, old, cur))
        else:
            churn.append((rel, old, cur))
    unchanged = len(results) - len(real) - len(churn) - len(new)

    print(f"\n④ 与基线比对：基线 {len(base)} 条 / 本次 {len(results)} 条")
    print(f"     同图同判（无变化）  {unchanged}")
    print(f"     新增截图            {len(new)}")
    print(f"     内容已换（临时截图被覆写，不算回归） {len(churn)}")
    print(f"     **同图判定变化（真回归）** {len(real)}")

    for rel, old, cur in real:
        print(f"     ❌ [真回归] {rel:34s} {old['page']}({old['score']}) → {cur['page']}({cur['score']})")
    for rel, old, cur in churn:
        print(f"     ·  [换图]   {rel:34s} {old['page']}({old['score']}) → {cur['page']}({cur['score']})")
    for rel, cur in new:
        print(f"     +  [新图]   {rel:34s} → {cur['page']}({cur['score']}) mean={cur['mean']}")

    if real:
        print("     ⚠️ 同一张图判定了不同的页面 —— 这是**真回归**，必须解释；"
              "确认无误后用 --update 刷新基线")
        bad = 1
    else:
        print("     ✅ 没有同图判定变化（换图/新图属正常，因为 runtime 截图会被反复覆写）")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
