#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手动逐节点读面板（不依赖任何"页面必须=活动地图"的守卫）—— 主线地图也能用。

用法: python tools/panel_probe.py "R8-1=109,254" "R8-2=393,254" ...
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

SVC = "http://127.0.0.1:8765"


def svc(path: str, timeout: int = 25):
    with urllib.request.urlopen(SVC + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    out = {}
    for spec in sys.argv[1:]:
        code, _, pos = spec.partition("=")
        x, y = (int(v) for v in pos.split(","))
        # 先确认当前在哪：若在关卡详情页，先 BACK 回地图
        cur = svc("/all")
        if cur.get("page") == "关卡详情":
            svc("/key?code=4")
            time.sleep(1.2)
        svc(f"/tap?x={x}&y={y}")
        time.sleep(2.2)
        st = svc("/all")
        rec = {k: st.get(k) for k in ("page", "stars", "cleared", "reward_primes", "reward_score")}
        rec["texts"] = (svc("/look").get("texts") or [])[:6]
        out[code] = rec
        verdict = "★有矿" if rec.get("reward_primes") else ("已三星" if rec.get("stars") == 3 else "?")
        print(f"{code:8s} page={rec['page']} stars={rec['stars']} 徽章={rec['reward_primes']} "
              f"({rec['reward_score']}) ⇒ {verdict} | OCR={rec['texts']}", flush=True)
    with open("runtime/reports/panel_probe.json", "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    print("已追加写 runtime/reports/panel_probe.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
