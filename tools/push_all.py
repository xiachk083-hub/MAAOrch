"""三号同时/依次启动推图 —— "一起跑"的入口（验证泛化用）。

用法:
    python tools/push_all.py                 # 每个号按状态自动挑下一关，**同时**发起
    python tools/push_all.py --serial        # 依次发起（一个跑完再下一个）
    python tools/push_all.py --stage 32822410=SR-1 --stage 751737728=EX-4   # 指定
    python tools/push_all.py --dry           # 只看会发什么，不真发

为什么要它（用户 2026-09-17）: 页面只能一个一个点"重试"；要验证"三个号同时跑"到底稳不稳
（MAA/adb/通道 会不会互相抢），必须有一个"三号一起启动"的入口。**它本身不花理智**，
真花理智的是被点起来的那几关（失败仍返还）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import threading
import time
import urllib.request

PROJ = pathlib.Path(__file__).resolve().parent.parent
ACCOUNTS = PROJ / "data" / "accounts.json"
SR54 = pathlib.Path(r"C:\Users\xiach\.codex\visualizations\2026\09\16"
                    r"\01a0aaf9-9a07-7931-b068-f38e4064ea0f\sr54")


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def next_stage(acc: dict) -> str:
    """这个号下一个该打哪关：SR-1..8、SR-ST-2、EX-1..8 依次找第一个没通关的。"""
    st = load(SR54 / acc.get("state", ""), {}).get("stages", {})
    for key in [f"SR-{i}" for i in range(1, 9)] + [f"EX-{i}" for i in range(1, 9)]:
        if (st.get(key) or {}).get("status") not in ("三星", "二星"):
            return key
    return ""


def fire(acc: dict, stage: str, out: list) -> None:
    url = f"http://127.0.0.1:{acc['channel_port']}/retry/{stage}"
    try:
        req = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
        out.append((acc["name"], stage, r.status, body[:120]))
    except Exception as e:
        out.append((acc["name"], stage, 0, f"{type(e).__name__}: {e}"))


def main() -> None:
    ap = argparse.ArgumentParser(description="三号一起/依次启动（不花理智；花的是各关自己）")
    ap.add_argument("--serial", action="store_true", help="依次发起（一个跑完再下一个）")
    ap.add_argument("--stage", action="append", default=[], help="uid=关卡（可多次；默认自动挑）")
    ap.add_argument("--dry", action="store_true", help="只打印，不真发")
    a = ap.parse_args()

    accs = load(ACCOUNTS, {}).get("accounts", [])
    want = dict(x.split("=", 1) for x in a.stage)
    tasks = [(acc, want.get(acc["uid"]) or next_stage(acc)) for acc in accs]
    tasks = [(acc, s) for acc, s in tasks if s]
    if not tasks:
        print("没有可打的关（都通关了）")
        return

    for acc, s in tasks:
        print(f"  {acc['name']:6s} {acc['client']:4s} 通道 :{acc['channel_port']} → {s}")
    if a.dry:
        print("（--dry：没真发）")
        return

    out: list = []
    t0 = time.time()
    if a.serial:
        for acc, s in tasks:
            fire(acc, s, out)
    else:
        ths = [threading.Thread(target=fire, args=(acc, s, out)) for acc, s in tasks]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    print(f"\n同时发起完成（{time.time() - t0:.1f}s）：")
    for name, s, code, body in out:
        print(f"  {name:6s} {s:5s} HTTP {code}  {body}")


if __name__ == "__main__":
    main()
