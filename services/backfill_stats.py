"""Backfill stats.json from events.log — recover run records for days when
save_run wasn't firing (stats.py was guarded by `if tasks:` and Core-direct
runs passed empty lists).

Run on the target machine:  python backfill_stats.py [days_back]
Reads events.log, extracts [清理] ... exit=0 (success) and [启动] ... 启动失败
(failure) events, maps names to account ids via models/config.json, and
appends one run record per event to accounts/{id}/stats.json.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJ = Path(__file__).parent.parent
EVENTS = PROJ / "events.log"
CONFIG = PROJ / "models" / "config.json"
STATS_DIR = PROJ / "models" / "accounts"

# [清理] 明日方舟-官-21 exit=0   (完成)
RE_DONE = re.compile(r'"t": "([\d-]+ [\d:]+)".*?\[清理\] 明日方舟-(\S+) exit=0')
# [启动] 3d3bce 启动失败，退回队列等待重试   (失败)
RE_FAIL = re.compile(r'"t": "([\d-]+ [\d:]+)".*?\[启动\] ([0-9a-f]{12}) 启动失败')


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    cutoff = datetime.now() - timedelta(days=days)

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    name2id = {a.get("name", "").replace("明日方舟-", ""): a.get("id")
               for a in cfg.get("accounts", []) if a.get("id")}

    done: list[tuple[str, str, str]] = []   # (aid, ts, name)
    fail: list[tuple[str, str, str]] = []   # (aid, ts, name)

    data = EVENTS.read_text(encoding="utf-8", errors="replace")
    for m in RE_DONE.finditer(data):
        ts = m.group(1)
        try:
            if datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") < cutoff:
                continue
        except ValueError:
            continue
        name = m.group(2)
        aid = name2id.get(name, "")
        if aid:
            done.append((aid, ts, name))
    for m in RE_FAIL.finditer(data):
        ts = m.group(1)
        try:
            if datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") < cutoff:
                continue
        except ValueError:
            continue
        aid = m.group(2)
        name = next((n for n, i in name2id.items() if i == aid), aid[:6])
        fail.append((aid, ts, name))

    written = 0
    for aid, ts, name in done + fail:
        safe = re.sub(r'[^\w.-]', '_', aid) or "_"
        sp = STATS_DIR / safe / "stats.json"
        try:
            rec = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        except Exception:
            rec = {}
        runs = rec.setdefault("runs", [])
        # 去重（同 ts 同账号已存在则跳过）
        if any(r.get("ts") == ts for r in runs):
            continue
        runs.append({
            "ts": ts,
            "end_ts": ts,
            "elapsed": 0,
            "status": "完成" if (aid, ts, name) in done else "失败",
            "exit_code": 0 if (aid, ts, name) in done else 1,
            "tasks": {},
            "drops": {},
            "backfilled": True,
        })
        runs.sort(key=lambda r: r.get("ts", ""))
        try:
            sp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            written += 1
        except Exception as e:
            print("FAIL", sp, e)

    print(f"回填完成: {len(done)} 成功 + {len(fail)} 失败 = {written} 条记录")


if __name__ == "__main__":
    main()
