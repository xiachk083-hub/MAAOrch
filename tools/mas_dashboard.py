#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAS 终端看板 — 汇总 AUTO-MAS 的运行 / 剿灭 / 刷关 / 耗时 / 失败数据。

读取 AUTO-MAS 主机的配置与历史目录，输出纯文本看板（也可 --json 供其它程序消费）。

用法（在 AUTO-MAS 主机上）:
    python mas_dashboard.py                 # 今日 + 近 7 天
    python mas_dashboard.py --days 3        # 调整窗口
    python mas_dashboard.py --json          # 机器可读
    python mas_dashboard.py --top 20        # 各榜单条数

本地快捷调用（Git Bash）: bash tools/mas_dash.sh
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.environ.get("MAS_ROOT", r"E:\AUTO-MAS")
HIST = os.path.join(ROOT, "history")
CFG_PATH = os.path.join(ROOT, "config", "ScriptConfig.json")

DROP_RE = re.compile(r"(\S+) 掉落统计")
JADE_RE = re.compile(r"剿灭模式\s*[:：]\s*(\d+)\s*/\s*(\d+)")
START_RE = re.compile(r"Start Task Chain: (\w+), Task ID: (\d+)")
DONE_RE = re.compile(r"Completed Task Chain: (\w+), Task ID: (\d+)")
ERR_RE = re.compile(r"任务出错: (\w+)")
TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3}\]")
ACT_RE = re.compile(r"开始行动 (\d+)(?:~(\d+))? 次")

CHAIN_ZH = {
    "StartUp": "开始唤醒", "Fight": "理智作战", "Infrast": "基建换班",
    "Recruit": "自动公招", "Mall": "信用收支", "Award": "领取奖励",
    "Roguelike": "自动肉鸽", "DepotMaintain": "库存保持",
}

# ── 显示宽度（中文按 2 列） ────────────────────────────────────────────────
def dw(text) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))


def pad(text, width: int) -> str:
    text = str(text)
    return text + " " * max(0, width - dw(text))


def rpad(text, width: int) -> str:
    text = str(text)
    return " " * max(0, width - dw(text)) + text


def bar(done: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "-" * width
    filled = int(round(width * min(1.0, done / total)))
    return "█" * filled + "·" * (width - filled)


# ── 配置 ───────────────────────────────────────────────────────────────────
def load_accounts() -> list[dict]:
    """从 ScriptConfig.json 取全部账号（含剿灭设置与周记录）。"""
    try:
        cfg = json.loads(open(CFG_PATH, encoding="utf-8", errors="replace").read())
    except Exception:
        return []
    out = []
    for sid, script in cfg.items():
        if sid == "instances" or not isinstance(script, dict):
            continue
        sname = (script.get("Info") or {}).get("Name", "?")
        idx = (script.get("Emulator") or {}).get("Index", "?")
        users = script.get("SubConfigsInfo", {}).get("UserData", {})
        for it in users.get("instances", []):
            u = users.get(it.get("uid"), {})
            info = u.get("Info") or {}
            data = u.get("Data") or {}
            task = u.get("Task") or {}
            out.append({
                "name": info.get("Name", "?"),
                "script": sname,
                "emu_index": str(idx),
                "server": info.get("Server", "?"),
                "enabled": bool(info.get("Status")),
                "annihilation": info.get("Annihilation", ""),
                "stage": info.get("Stage", "-"),
                "stage_remain": info.get("Stage_Remain", "-"),
                "medicine": info.get("MedicineNumb", 0),
                "week_done": str(data.get("AnnihilationCompletedWeek") or ""),
                "notes": info.get("Notes", ""),
                "tasks": {k: bool(task.get(k)) for k in
                          ("IfFight", "IfInfrast", "IfRecruit", "IfMall", "IfAward", "IfRoguelike")},
            })
    return out


# ── 历史扫描 ───────────────────────────────────────────────────────────────
def scan_history(days: int, anni_days: int = 9) -> tuple[dict, list]:
    """遍历历史，返回 (每账号聚合, 失败明细)。

    运行/刷关/耗时 只统计近 `days` 天；剿灭进度始终回溯 `anni_days` 天，
    以免窗口小于一周时低估本周剿灭进度。
    """
    today = datetime.now().date()
    win = {(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)}
    anni_win = {(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(anni_days)}
    agg: dict[str, dict] = defaultdict(lambda: {
        "runs": [], "stages": Counter(), "jade": 0, "jade_day": "",
        "chains": defaultdict(list), "fail": 0,
    })
    failures: list[dict] = []

    for day in sorted(win | anni_win, reverse=True):
        in_win = day in win
        droot = os.path.join(HIST, day)
        if not os.path.isdir(droot):
            continue
        for acct in os.listdir(droot):
            adir = os.path.join(droot, acct)
            if not os.path.isdir(adir):
                continue
            A = agg[acct]
            if in_win:
                for jf in glob.glob(os.path.join(adir, "*.json")):
                    try:
                        j = json.loads(open(jf, encoding="utf-8", errors="replace").read())
                    except Exception:
                        continue
                    res = j.get("maa_result", "?")
                    ts = datetime.fromtimestamp(os.path.getmtime(jf))
                    A["runs"].append({"day": day, "ts": ts.strftime("%m-%d %H:%M"), "result": res})
                    if res != "Success!":
                        A["fail"] += 1
                        failures.append({"day": day, "ts": ts.strftime("%m-%d %H:%M"),
                                         "account": acct, "result": res})
            for lf in glob.glob(os.path.join(adir, "*.log")):
                try:
                    if os.path.getsize(lf) > 4_000_000:
                        continue
                    txt = open(lf, encoding="utf-8", errors="replace").read()
                except Exception:
                    continue
                if in_win:
                    for m in DROP_RE.finditer(txt):
                        A["stages"][m.group(1)] += 1
                for m in JADE_RE.finditer(txt):
                    v = int(m.group(1))
                    if v > A["jade"]:
                        A["jade"] = v
                        A["jade_day"] = day
                if not in_win:
                    continue
                chains = {}
                for line in txt.splitlines():
                    tsm = TS_RE.match(line)
                    if not tsm:
                        continue
                    t = datetime.strptime(tsm.group(1), "%Y-%m-%d %H:%M:%S")
                    sm, dm = START_RE.search(line), DONE_RE.search(line)
                    if sm:
                        chains[sm.group(2)] = [sm.group(1), t]
                    elif dm:
                        key = dm.group(2)
                        if key in chains:
                            nm, st = chains.pop(key)
                            A["chains"][nm].append((t - st).total_seconds())
                    else:
                        em = ERR_RE.search(line)
                        if em:
                            for key, (nm, st) in list(chains.items()):
                                A["chains"][nm].append((t - st).total_seconds())
                                chains.pop(key, None)
                                break
    return agg, failures


# ── 周标记（与 AUTO-MAS 一致：UTC+4） ──────────────────────────────────────
def week_marker() -> str:
    now = datetime.now(timezone.utc) + timedelta(hours=4)
    iso = now.isocalendar()
    return "%04d-W%02d" % (iso[0], iso[1])


# ── 渲染 ───────────────────────────────────────────────────────────────────
def render(accounts: list[dict], agg: dict, failures: list, days: int, top: int) -> str:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    WEEK = week_marker()
    L: list[str] = []
    add = L.append

    active = [a for a in accounts if a["enabled"]]
    anni_accts = [a for a in active if a["annihilation"] and a["annihilation"] != "Close"]

    def _jade(a) -> int:
        return int((agg.get(a["name"], {}) or {}).get("jade") or 0)

    # 打满判据：本周日志里出现过 1800/1800，或周记录已登记本周
    anni_full = [a for a in anni_accts if _jade(a) >= 1800 or a["week_done"] == WEEK]
    anni_open = [a for a in anni_accts if a not in anni_full]
    anni_pending_mark = [a for a in anni_accts if _jade(a) >= 1800 and a["week_done"] != WEEK]

    today_accts_ran = {nm for nm, A in agg.items()
                       for r in (A.get("runs") or []) if r["day"] == today}
    today_runs = sum(1 for A in agg.values() for r in (A.get("runs") or []) if r["day"] == today)
    today_ok = sum(1 for A in agg.values() for r in (A.get("runs") or [])
                   if r["day"] == today and r["result"] == "Success!")
    today_fail = today_runs - today_ok
    win_fail = len(failures)

    add("═" * 78)
    add("  MAS 终端看板   " + now.strftime("%Y-%m-%d %H:%M") + "   窗口 " + str(days) + " 天")
    add("═" * 78)
    add("【总览】账号 %d（启用 %d）｜今日已跑账号 %d/%d，运行 %d 次（成功 %d / 失败 %d）｜窗口内失败 %d"
        % (len(accounts), len(active), len(today_accts_ran), len(active),
           today_runs, today_ok, today_fail, win_fail))
    add("        本周剿灭（%s）满 %d / 未满 %d｜不刷 %d%s"
        % (WEEK, len(anni_full), len(anni_open),
           len([a for a in active if a["annihilation"] == "Close"]),
           ("｜⚠ %d 个已达 1800 待登记周记录" % len(anni_pending_mark)) if anni_pending_mark else ""))

    # 剿灭进度
    add("")
    add("─ 本周剿灭进度 ─".ljust(40, "─"))
    for a in sorted(anni_open, key=lambda x: -_jade(x)):
        jade = _jade(a)
        detail = ("%d/1800" % jade) if jade else "无记录"
        wall = " ·" if a["week_done"] else "  "          # 空格=无登记, ·=有登记
        add("  ⏳ %s %s %s%s" % (pad(a["name"], 22), pad(bar(jade, 1800), 14), detail, wall))
    if not anni_open:
        add("  ✅ 全部打满")
    if anni_full:
        add("  ✅ 已满 %d 个：%s" % (len(anni_full), "、".join(a["name"] for a in anni_full[:top])))

    # 刷关分布
    add("")
    add("─ 刷关分布（窗口内·掉落统计口径）─".ljust(40, "─"))
    rows = []
    for a in accounts:
        A = agg.get(a["name"], {})
        st = A.get("stages") or Counter()
        if st:
            rows.append((sum(st.values()), a["name"], st))
    for total, name, st in sorted(rows, reverse=True)[:top]:
        desc = "  ".join("%s×%d" % (k, v) for k, v in st.most_common(5))
        add("  %s %s" % (pad(name, 22), desc))

    # 任务耗时
    add("")
    add("─ 任务耗时（窗口内）─".ljust(40, "─"))
    chainsum: dict[str, list] = defaultdict(list)
    for A in agg.values():
        for nm, durs in (A.get("chains") or {}).items():
            chainsum[nm].extend(durs)
    add("  %s %s %s %s %s" % (pad("任务", 12), rpad("次数", 6), rpad("平均", 9), rpad("最长", 9), rpad("合计", 9)))
    for nm, durs in sorted(chainsum.items(), key=lambda x: -sum(x[1])):
        if not durs:
            continue
        add("  %s %s %s %s %s" % (
            pad(CHAIN_ZH.get(nm, nm), 12), rpad(len(durs), 6),
            rpad("%.1fmin" % (sum(durs) / len(durs) / 60), 9),
            rpad("%.1fmin" % (max(durs) / 60), 9),
            rpad("%.1fmin" % (sum(durs) / 60), 9)))

    # 失败原因
    add("")
    add("─ 失败原因（窗口内）─".ljust(40, "─"))
    reasons = Counter(f["result"] for f in failures)
    for r, c in reasons.most_common(top):
        add("  %s %d" % (pad(r, 30), c))
    if failures:
        add("  最近失败：")
        for f in failures[:min(top, 12)]:
            add("    %s %s %s" % (f["ts"], pad(f["account"], 20), f["result"]))

    # 单号速览
    add("")
    add("─ 账号速览（今日）─".ljust(40, "─"))
    add("  %s %s %s %s %s" % (pad("账号", 22), pad("今日", 18), pad("本周剿灭", 12), pad("剩余", 8), "主关"))
    for a in sorted(accounts, key=lambda x: (not x["enabled"], x["name"])):
        if not a["enabled"]:
            continue
        A = agg.get(a["name"], {})
        runs = [r for r in (A.get("runs") or []) if r["day"] == today]
        if runs:
            last = runs[-1]
            mark = "✅" if last["result"] == "Success!" else "❌"
            today_txt = "%s %s×%d" % (mark, last["ts"], len(runs))
        else:
            today_txt = "— 未跑"
        jade = A.get("jade") or 0
        anni_txt = ("Close" if a["annihilation"] == "Close"
                    else ("%d/1800" % jade if jade else "0/1800"))
        add("  %s %s %s %s %s" % (
            pad(a["name"], 22), pad(today_txt, 18), pad(anni_txt, 12),
            pad(a["stage_remain"], 8), a["stage"]))

    add("═" * 78)
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="MAS 终端看板")
    ap.add_argument("--days", type=int, default=7, help="统计窗口天数（默认 7）")
    ap.add_argument("--top", type=int, default=12, help="各榜单条数（默认 12）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    accounts = load_accounts()
    agg, failures = scan_history(args.days)

    if args.json:
        payload = {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "window_days": args.days,
            "week": week_marker(),
            "accounts": accounts,
            "failures": failures,
            "agg": {k: {
                "runs": v["runs"][-10:],
                "fail": v["fail"],
                "jade": v["jade"],
                "jade_day": v["jade_day"],
                "stages": dict(v["stages"]),
                "chains": {n: [round(x, 1) for x in d] for n, d in v["chains"].items()},
            } for k, v in agg.items()},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return

    print(render(accounts, agg, failures, args.days, args.top))


if __name__ == "__main__":
    main()
