"""多账号推图调度：每个号差多少理智、要等多久、来不来得及、先打哪个。

用法:
    python tools/push_schedule.py                # 读三个号的状态，算调度
    python tools/push_schedule.py --fetch        # 顺便从各模拟器上 OCR 读一次当前理智

为什么单独成工具（用户 2026-09-17）: 三个号共用一个"推图 Agent"，理智回复慢（10/小时），
同一时刻只有一个号值得开跑 ⇒ 需要一个"谁先打、谁还得等"的调度表。**不花理智，纯计算。**
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
SR54 = pathlib.Path(r"C:\Users\xiach\.codex\visualizations\2026\09\16"
                    r"\01a0aaf9-9a07-7931-b068-f38e4064ea0f\sr54")
ACCOUNTS = PROJ / "data" / "accounts.json"
STAGE_TABLE = PROJ / "data" / "_stage_table.json"
SANITY_FILE = PROJ / "data" / "sanity.json"
EVENT_DEADLINE = dt.datetime(2026, 9, 18, 3, 59)      # 活动结束（界面写的）
REGEN_PER_HOUR = 10.0                                  # 理智回复速度


def stage_name(key: str) -> str:
    """状态里的键 → 关卡表里的 stage_name：SR-8 → act54side_08 ｜ EX-1 → act54side_ex01。"""
    kind, num = key.split("-")
    return f"act54side_ex{int(num):02d}" if kind.upper() == "EX" else f"act54side_{int(num):02d}"


def ap_cost(stage_name_: str) -> int:
    try:
        d = json.loads(STAGE_TABLE.read_text(encoding="utf-8"))["stages"][stage_name_]
        return int(d.get("apCost") or 0)
    except Exception:
        return 0


def load_state(acc: dict) -> dict:
    p = SR54 / acc.get("state", "")
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("stages") or {}
    except Exception:
        return {}


def load_sanity() -> dict:
    try:
        return json.loads(SANITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update_sanity_file(values: dict) -> None:
    cur = load_sanity()
    cur.update(values)
    SANITY_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_sanity(acc: dict) -> int | None:
    """从模拟器上 OCR 读一次理智（挂在终端/详情页时 "39/210" 这种）。读不到就 None。"""
    try:
        sys.path.insert(0, str(PROJ))
        sys.path.insert(0, str(PROJ / "services" / "ai_agent"))
        from fastshot import find_adb, nc_capture            # noqa: PLC0415
        from infrastructure.ocr import ocr_boxes             # noqa: PLC0415
        f = nc_capture(find_adb(), f"127.0.0.1:{acc['emu_port']}", timeout=6.0)
        if f is None:
            return None
        for b in (ocr_boxes(f) or []):
            m = re.search(r"(\d{1,3})\s*/\s*210", b["text"])
            if m:
                return int(m.group(1))
    except Exception:
        return None
    return None


def plan(acc: dict) -> dict:
    st = load_state(acc)
    done, need, left = 0, 0, []
    # **按现实顺序**列剩余：SR-1..8 依次（SR-EX 要等 SR 全通 + SR-ST-2），再 EX-1..8 依次 ——
    # 顺序影响"现在能连打几关"（不能跳关打），所以不能按便宜的先数。
    for key in ([f"SR-{i}" for i in range(1, 9)] + [f"EX-{i}" for i in range(1, 9)]):
        rec = st.get(key) or {}
        if rec.get("status") in ("三星", "二星", "通关"):     # 通关（含星级未核）都算过了
            done += 1
            continue
        cost = ap_cost(stage_name(key))
        need += cost
        left.append((key, cost))
    return {"done": done, "total": 16, "need": need, "left": left,
            "sanity": int(load_sanity().get(acc["uid"], -1))}


def main() -> None:
    ap = argparse.ArgumentParser(description="多账号推图调度（不花理智）")
    ap.add_argument("--fetch", action="store_true", help="顺便从各模拟器 OCR 读一次理智")
    a = ap.parse_args()
    accs = json.loads(ACCOUNTS.read_text(encoding="utf-8"))["accounts"]

    if a.fetch:
        got = {}
        for acc in accs:
            v = fetch_sanity(acc)
            if v is not None:
                got[acc["uid"]] = v
                print(f"  [理智] {acc['name']}（{acc['client']}）读到 {v}")
        if got:
            update_sanity_file(got)

    now = dt.datetime.now()
    hours_left = max(0.0, (EVENT_DEADLINE - now).total_seconds() / 3600.0)
    regen = hours_left * REGEN_PER_HOUR

    print(f"\n活动截止 {EVENT_DEADLINE:%m-%d %H:%M} ｜ 还剩 {hours_left:.1f} 小时"
          f" ｜ 自然回复约 {regen:.0f} 理智\n")
    rows = []
    for acc in accs:
        p = plan(acc)
        have = p["sanity"]
        have_s = f"{have}" if have >= 0 else "?"
        total_can = (have + regen) if have >= 0 else None
        if p["need"] == 0:
            verdict, wait = "✅ 已完成", "—"
        elif have < 0:
            verdict, wait = "？ 理智未知（跑 --fetch）", "?"
        elif have >= p["need"]:
            verdict, wait = "✅ 现在就能打完", "0h"
        elif total_can is not None and total_can >= p["need"]:
            verdict = "⏳ 来得及（要等回复）"
            wait = f"{(p['need'] - have) / REGEN_PER_HOUR:.1f}h"
        else:
            verdict = "⚠️ 来不及（按现在的回复速度）"
            wait = "—"
        rows.append([acc["name"], acc["client"], f"{p['done']}/{p['total']}",
                     p["need"], have_s, verdict, wait])

    head = ["号", "客户端", "进度", "还差", "现有", "结论", "需等"]
    widths = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
    line = "  ".join(str(h).ljust(widths[i]) for i, h in enumerate(head))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)))

    # 建议：谁"现在能连打的关数"最多就先打谁（理智回复慢，同一时刻只值得开一个号）
    def affordable(p: dict) -> int:
        """现在能连打几关：**按顺序**走（跳过一关去打后面的不成立）"""
        if p["sanity"] < 0:
            return 0
        n, left_sanity = 0, p["sanity"]
        for _key, c in p["left"]:
            if left_sanity >= c:
                left_sanity -= c
                n += 1
            else:
                break                      # 这一关都打不起 ⇒ 后面的更打不了（要按顺序）
        return n

    cand = sorted([(acc, plan(acc)) for acc in accs],
                  key=lambda x: -affordable(x[1]))
    if cand and affordable(cand[0][1]) > 0:
        acc, p = cand[0]
        print(f"\n建议先打：**{acc['name']}（{acc['client']}）** —— 现在能连打 "
              f"{affordable(p)} 关（还剩 {len(p['left'])} 关）")
    else:
        print("\n建议：现在都打不了（理智不够或未知），先等回复或跑一次 --fetch 读理智")


if __name__ == "__main__":
    main()
