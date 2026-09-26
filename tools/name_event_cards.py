#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**给模板库的活动卡认名**（用视觉模型，离线）。

背景：模板库 data/templates/events/*.png 有 14 张卡，但只有 3 张知道对应哪个 act
（buyizhicai=act28side、hongsirong=act43side、xinhuo=act41side）。要打 LE/PV/MT/EP 就必须先认名。

做法：对每张模板问视觉模型"这是明日方舟哪个活动/关卡代号前缀"，把结果落
`data/templates/events/labels_vision.json`（**不确定就标 unknown，不许猜**）。

用法: python tools/name_event_cards.py [--only slug1 slug2]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
TPL = ROOT / "data" / "templates" / "events"
OUT = TPL / "labels_vision.json"

KNOWN_ACTS = {
    "act28side": "CV", "act43side": "AD", "act41side": "EA",
    "act18side": "LE", "act38side": "PV", "act42side": "MT", "act39side": "EP",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[])
    a = ap.parse_args()

    sys.path.insert(0, str(ROOT / "tools"))
    import vision as v

    files = sorted(p for p in TPL.glob("*.png"))
    if a.only:
        files = [p for p in files if p.stem in set(a.only)]
    out = {}
    if OUT.exists():
        try:
            out = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            out = {}

    for p in files:
        if p.stem in out and out[p.stem].get("name"):
            print(f"{p.stem}: 已有 → {out[p.stem]}", flush=True)
            continue
        # **注意**：prompt 里不要放引号/花括号 —— Windows 下 subprocess 传参会被拆坏，
        # modlens 收到残缺 prompt 就返回空（实测：同一张图用简单问法能答对"丛林症结"）。
        q = ("这是明日方舟的活动卡片截图。请只回答一行，格式：活动中文名|关卡代号前缀。"
             "前缀形如 LE/PV/MT/EP/CV/AD/EA；不确定就写成 ?。不要输出其它内容。")
        ans = v.ask(p, q)
        rec = {"raw": ans[:200], "name": "", "prefix": "?"}
        line = next((ln for ln in (ans or "").splitlines() if "|" in ln), "")
        if line:
            name, _, pre = line.partition("|")
            rec["name"] = name.strip().strip("。 .")
            rec["prefix"] = (pre.strip().strip("。 .") or "?").upper()[:6]
        out[p.stem] = rec
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{p.stem}: name={rec.get('name')} prefix={rec.get('prefix')}", flush=True)

    print("\n=== 汇总（slug → 活动名 / 前缀）===")
    for k, rec in sorted(out.items()):
        print(f"  {k:14s} {str(rec.get('name'))[:26]:28s} {rec.get('prefix')}")
    print("落盘:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
