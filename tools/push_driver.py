"""推图驱动器：哪个号空着就给它派下一关，**打完立刻读结果、立刻派下一关**（不用人盯）。

用法:
    python tools/push_driver.py                      # 三号并行，各自连续推
    python tools/push_driver.py --uid 394162955      # 只推一个号
    python tools/push_driver.py --max-stages 3       # 每个号最多推几关（默认 3）
    python tools/push_driver.py --minutes 25         # 总时长上限（默认 25 分钟）
    python tools/push_driver.py --dry                # 只看会派什么

为什么要有它（用户 2026-09-17："那边打完了你也没有及时去看"）:
    之前是"发一次 → 我过一会儿才回来读日志"，中间空转。这脚本补的就是这段：
    轮询各号的 /status（**不截图、不 OCR，纯 HTTP + 读文件，开销可忽略**），
    一空下来就派下一关，并且把每关结果记进 data/push_driver_log.jsonl。

失败保护: 同一关连续失败就跳过（记进 skipped），避免"编队缺人"那种原地打转。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import threading
import time
import urllib.request

PROJ = pathlib.Path(__file__).resolve().parent.parent
ACCOUNTS = PROJ / "data" / "accounts.json"
SR54 = pathlib.Path(r"C:\Users\xiach\.codex\visualizations\2026\09\16"
                    r"\01a0aaf9-9a07-7931-b068-f38e4064ea0f\sr54")
SANITY = PROJ / "data" / "sanity.json"
JOURNAL = PROJ / "data" / "push_driver_log.jsonl"
STAGE_ORDER = [f"SR-{i}" for i in range(1, 9)] + [f"EX-{i}" for i in range(1, 9)]
PRINT_LOCK = threading.Lock()


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def say(acc: dict, msg: str) -> None:
    with PRINT_LOCK:
        print(f"[{dt.datetime.now():%H:%M:%S}] {acc['name']:6s} {msg}", flush=True)


def journal(rec: dict) -> None:
    with PRINT_LOCK:
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def status(acc: dict) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{acc['channel_port']}/status", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def fire(acc: dict, stage: str) -> str:
    """派单 → "ok" / "busy"（这个号正在打，409）/ "fail"。

    为什么单独区分 busy（2026-09-17 实测踩到）: 通道对一个正在跑的号返回 409，
    我原来当成"派单失败"直接收工 ⇒ 看起来就是"永远不会自己接下一关" ✗。
    """
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{acc['channel_port']}/retry/{stage}",
                                     method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=30) as r:
            return "ok" if r.status == 200 else "fail"
    except urllib.error.HTTPError as e:
        return "busy" if e.code == 409 else "fail"
    except Exception as e:
        say(acc, f"派单失败 {stage}: {type(e).__name__}")
        return "fail"


def state_of(acc: dict) -> dict:
    return load(SR54 / acc.get("state", ""), {}).get("stages", {})


def next_stage(acc: dict, skipped: set) -> str:
    st = state_of(acc)
    for key in STAGE_ORDER:
        rec = st.get(key) or {}
        if key in skipped or rec.get("status") == "未解锁":   # 没解锁的别去撞锁
            continue
        if rec.get("status") not in ("三星", "二星", "通关"):
            return key
    return ""


def sanity_left(acc: dict) -> int:
    v = load(SANITY, {}).get(acc["uid"])
    return int(v) if isinstance(v, (int, float)) else -1


def spend(acc: dict, cost: int) -> None:
    d = load(SANITY, {})
    if isinstance(d.get(acc["uid"]), (int, float)):
        d[acc["uid"]] = max(0, int(d[acc["uid"]]) - cost)
        SANITY.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def stage_cost(acc: dict, stage: str) -> int:
    try:
        kind, num = stage.split("-")
        sn = (f"act54side_ex{int(num):02d}" if kind.upper() == "EX" else f"act54side_{int(num):02d}")
        tab = json.loads((PROJ / "data" / "_stage_table.json").read_text(encoding="utf-8"))["stages"]
        return int((tab.get(sn) or {}).get("apCost") or 0)
    except Exception:
        return 0


def drive(acc: dict, max_stages: int, poll: float, deadline: float) -> None:
    skipped: set = set()
    done = 0
    while done < max_stages and time.time() < deadline:
        stage = next_stage(acc, skipped)
        if not stage:
            say(acc, "没有可打的关了（都通关或被跳过）")
            return
        have = sanity_left(acc)
        cost = stage_cost(acc, stage)
        if have >= 0 and have < cost:
            say(acc, f"理智不够打 {stage}（{have} < {cost}）—— 等回复")
            return
        say(acc, f"派单 {stage}（理智 {have if have >= 0 else '?'} ÷ 本关 {cost}）")
        r = fire(acc, stage)
        if r == "busy":                     # 已经在打 → 等它打完，再回到循环头接着派
            while time.time() < deadline:
                time.sleep(poll)
                if not status(acc).get("running"):
                    break
            continue
        if r == "fail":
            return
        t0 = time.time()
        while time.time() < deadline:
            time.sleep(poll)
            st = status(acc)
            if not st.get("running"):
                break
        rec = state_of(acc).get(stage) or {}
        outcome = rec.get("status", "")
        reason = rec.get("reason", "")
        secs = round(time.time() - t0)
        journal({"ts": dt.datetime.now().isoformat(timespec="seconds"), "uid": acc["uid"],
                 "name": acc["name"], "stage": stage, "status": outcome, "reason": reason,
                 "job": rec.get("job_used", ""), "seconds": secs})
        if outcome in ("三星", "二星", "通关"):
            say(acc, f"{stage} ✅ {outcome}（{secs}s，作业 {rec.get('job_used', '')}）")
            spend(acc, cost)
            done += 1
        else:
            say(acc, f"{stage} ❌ {outcome} {reason}（{secs}s）→ 跳过这关，换下一关")
            skipped.add(stage)
        if time.time() >= deadline:
            return


def main() -> None:
    ap = argparse.ArgumentParser(description="推图驱动器（派单 + 及时收结果，不截图不 OCR）")
    ap.add_argument("--uid", default="", help="只推这个账号（默认全部）")
    ap.add_argument("--max-stages", type=int, default=3, help="每个号最多推几关")
    ap.add_argument("--minutes", type=float, default=25.0, help="总时长上限（分钟）")
    ap.add_argument("--poll", type=float, default=15.0, help="轮询间隔秒")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    accs = load(ACCOUNTS, {}).get("accounts", [])
    if a.uid:
        accs = [x for x in accs if x["uid"] == a.uid]
    if not accs:
        raise SystemExit("没有匹配的账号")
    if a.dry:
        for acc in accs:
            print(f"  {acc['name']:6s} → {next_stage(acc, set()) or '（无）'}"
                  f" ｜ 理智 {sanity_left(acc)}")
        return

    deadline = time.time() + a.minutes * 60
    print(f"推图驱动：{len(accs)} 个号并行 ｜ 每号最多 {a.max_stages} 关"
          f" ｜ 总时长 {a.minutes} 分钟 ｜ 轮询 {a.poll}s", flush=True)
    ths = [threading.Thread(target=drive, args=(acc, a.max_stages, a.poll, deadline), daemon=True)
           for acc in accs]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=max(0, deadline - time.time()) + 60)
    print("\n=== 各号状态 ===", flush=True)
    for acc in accs:
        st = state_of(acc)
        ok = sum(1 for k in STAGE_ORDER if (st.get(k) or {}).get("status") in ("三星", "二星", "通关"))
        print(f"  {acc['name']:6s} {acc['client']:4s} 已通关 {ok}/16 ｜ 理智 {sanity_left(acc)}")
    print(f"明细见 {JOURNAL}", flush=True)


if __name__ == "__main__":
    main()
