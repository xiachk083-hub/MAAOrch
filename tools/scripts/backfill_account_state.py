"""Backfill AccountState from archived MAA logs (maa_history/{aid}/).

AccountState started recording at deploy time — today's earlier runs have no
state. This parses today's archived asst.log files and reconstructs:
    last_login / last_use / last_complete / last_status / sanity / today_runs

Run on the target:  python backfill_account_state.py [days_back]
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJ = Path(__file__).parent.parent
HIST = PROJ / "logs" / "maa_history"
CONFIG = PROJ / "models" / "config.json"

RE_SANITY = re.compile(r'"current_sanity":\s*(\d+).*?"max_sanity":\s*(\d+)')
RE_TS = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3}\]')
RE_DROPS = re.compile(r'"itemName":\s*"([^"]+)".*?"quantity":\s*(\d+)')


def _parse_sanity(data: str) -> dict | None:
    """Last SanityBeforeStage event in the log."""
    matches = list(RE_SANITY.finditer(data))
    if not matches:
        return None
    m = matches[-1]
    return {"current": int(m.group(1)), "max": int(m.group(2)),
            "report_time": ""}


def _parse_drops(data: str) -> dict:
    drops: dict[str, int] = {}
    for m in RE_DROPS.finditer(data):
        try:
            drops[m.group(1)] = drops.get(m.group(1), 0) + int(m.group(2))
        except ValueError:
            pass
    return drops


def _parse_first_last_ts(data: str) -> tuple[str, str]:
    ts = RE_TS.findall(data)
    if not ts:
        return "", ""
    return ts[0], ts[-1]


def _looks_completed(data: str) -> bool:
    # MAA logs "all tasks completed" on normal finish
    return ("all tasks completed" in data.lower()
            or "ALL_TASKS_COMPLETED" in data
            or "全部任务完成" in data)


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    cutoff = datetime.now() - timedelta(days=days)

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    id2name = {a.get("id"): a.get("name", "").replace("明日方舟-", "")
               for a in cfg.get("accounts", []) if a.get("id")}

    written = 0
    for aid in sorted(os.listdir(HIST)):
        d = os.path.join(HIST, aid)
        if not os.path.isdir(d):
            continue
        name = id2name.get(aid, aid)
        files = []
        for f in sorted(os.listdir(d)):
            if not f.endswith("_asst.log"):
                continue
            ts_str = f[:8]
            try:
                fdt = datetime.strptime(ts_str, "%Y%m%d")
            except ValueError:
                continue
            if fdt >= cutoff:
                files.append(os.path.join(d, f))
        if not files:
            continue

        # 汇总今天的归档（可能多轮 — 取最后完整的一轮）
        last_login = ""
        last_complete = ""
        last_sanity = None
        last_drops = {}
        last_ok = False
        for f in files:
            try:
                data = open(f, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            first, last = _parse_first_last_ts(data)
            if first and (not last_login or first < last_login):
                pass  # keep earliest login
            if first and not last_login:
                last_login = first
            if last and last > last_complete:
                last_complete = last
            s = _parse_sanity(data)
            if s:
                last_sanity = s
            dd = _parse_drops(data)
            if dd:
                last_drops = dd
            if _looks_completed(data):
                last_ok = True

        # 写入 AccountState
        try:
            sys.path.insert(0, str(PROJ))
            from models.account_state import AccountState
            st = AccountState(aid)
            st._roll_day()
            if last_login:
                st._data["last_login"] = last_login
                st._data["last_use"] = last_complete or last_login
            if last_complete:
                st._data["last_complete"] = last_complete
                st._data["last_status"] = "完成" if last_ok else "失败"
                st._data["last_exit_code"] = 0 if last_ok else 1
            if last_sanity:
                st._data["sanity"] = last_sanity
            if last_drops:
                st._data["last_drops"] = last_drops
            st._data["today_runs"] = len(files)
            st._save()
            written += 1
            print(f"回填 {name}: 登录={last_login} 完成={last_ok} 体力={last_sanity}")
        except Exception as e:
            print(f"回填失败 {aid}: {e}")

    print(f"共回填 {written} 个账号")


if __name__ == "__main__":
    main()
