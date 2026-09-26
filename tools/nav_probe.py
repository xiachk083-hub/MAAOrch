#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用服务的 /nav（学到的坐标 + 代号核对）逐关导航并读面板 —— 比手点坐标稳。

用法: python tools/nav_probe.py AD-2 AD-3 AD-ST-1
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

SVC = "http://127.0.0.1:8765"


def svc(path: str, timeout: int = 40):
    with urllib.request.urlopen(SVC + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    out = {}
    for code in sys.argv[1:]:
        q = urllib.parse.urlencode({"stage": code})
        nav = svc(f"/nav?{q}")
        st = svc("/all")
        rec = {"nav_ok": nav.get("ok"), "page_after": nav.get("page_after"),
               "stage_match": (nav.get("stage_check") or {}).get("stage_match"),
               "nav_ms": nav.get("total_ms")}
        rec.update({k: st.get(k) for k in ("page", "stars", "cleared", "reward_primes", "reward_score")})
        out[code] = rec
        mine = "★有矿（徽章在）" if st.get("reward_primes") else ("已三星" if st.get("stars") == 3 else "?")
        print(f"{code:9s} nav={'OK' if nav.get('ok') else 'FAIL'} {nav.get('total_ms')}ms "
              f"page={st.get('page')} stars={st.get('stars')} 徽章={st.get('reward_primes')} "
              f"({st.get('reward_score')}) ⇒ {mine}", flush=True)
    with open("runtime/reports/nav_probe.json", "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    print("追加写 runtime/reports/nav_probe.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
