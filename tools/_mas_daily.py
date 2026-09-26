# -*- coding: utf-8 -*-
"""MAS 生产线日常（只读）。

只对 AUTO-MAS 的 HTTP API 发**读请求**（/api/info/version、/api/scripts/get、
/api/scripts/user/get、/api/history/search）——不连 SSH、不传文件、不发任何写请求。

status 实测取值只有 DONE / ERROR（见 tools/_history_0902b.json 全量统计）。

用法:
    python tools/_mas_daily.py                # 最近 7 天
    python tools/_mas_daily.py --days 3
    python tools/_mas_daily.py --host http://100.79.173.69:36163
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.request

DEFAULT_HOST = "http://100.79.173.69:36163"


def api(host: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        host + path,
        data=json.dumps(body if body is not None else {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def roster(host: str) -> dict[str, str]:
    """在册启用的账号 {账号名: 副本路径}（Status is True 才算启用）。"""
    sc = api(host, "/api/scripts/get", {"scriptId": None})
    out: dict[str, str] = {}
    for it in sc.get("index") or []:
        sid = it.get("uid")
        d = (sc.get("data") or {}).get(sid) or {}
        path = ((d.get("Info") or {}).get("Path") or "").rstrip("/").split("/")[-1].split("\\")[-1]
        u = api(host, "/api/scripts/user/get", {"scriptId": sid, "userId": None})
        for uid, uv in (u.get("data") or {}).items():
            if uid == "instances":
                continue
            info = uv.get("Info") or {}
            if info.get("Status") is True:
                out[info.get("Name", "?")] = path
    return out


def day_map(host: str, days: list[str]) -> dict[str, dict]:
    """{日期: {账号: {done: n, error: n, err: str}}}"""
    r = api(host, "/api/history/search",
            {"mode": "DAILY", "start_date": days[0], "end_date": days[-1]})
    raw = (r.get("data") or {})
    res: dict[str, dict] = {}
    for d in days:
        per: dict[str, dict] = {}
        for name, rec in (raw.get(d) or {}).items():
            idx = rec.get("index") or []
            done = sum(1 for i in idx if i.get("status") == "DONE")
            err = sum(1 for i in idx if i.get("status") == "ERROR")
            ei = rec.get("error_info")
            if isinstance(ei, str):
                s = ei
                try:
                    ei = json.loads(ei)
                except Exception:                          # noqa: BLE001
                    ei = {}
            else:
                s = json.dumps(ei, ensure_ascii=False) if isinstance(ei, (dict, list)) else ""
            per[name] = {"done": done, "error": err, "err": (s or "")[:200],
                         "eid": ei if isinstance(ei, dict) else {},
                         "runs": [(i.get("date", "")[-8:], i.get("status")) for i in idx]}
        res[d] = per
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--account", default=None, help="只细看某个账号的逐日轨迹")
    a = ap.parse_args()

    v = api(a.host, "/api/info/version")
    print("生产线:", a.host)
    print("版本:", (v.get("current_hash") or "")[:12],
          "| 构建时间:", v.get("current_time"), "| 有新版本:", v.get("if_need_update"))

    reg = roster(a.host)
    print("在册启用账号:", len(reg))

    today = dt.date.today()
    days = [(today - dt.timedelta(days=i)).isoformat() for i in range(a.days - 1, -1, -1)]
    dm = day_map(a.host, days)

    print("\n=== 逐日 ===")
    print("%-12s %6s %7s %7s %7s %6s %8s %8s" % ("日期", "有记录", "全成", "重试成", "全败", "没跑", "首跑", "末跑"))
    for d in days:
        per = dm[d]
        allok = sum(1 for x in per.values() if x["error"] == 0 and x["done"] > 0)
        retry = sum(1 for x in per.values() if x["error"] > 0 and x["done"] > 0)
        allbad = sum(1 for x in per.values() if x["error"] > 0 and x["done"] == 0)
        idle = len(reg) - len(per)
        ts = sorted(t for x in per.values() for t, _s in x["runs"])
        print("%-12s %6d %7d %7d %7d %6d %8s %8s" % (
            d, len(per), allok, retry, allbad, idle,
            ts[0][:5] if ts else "-", ts[-1][:5] if ts else "-"))

    print("\n=== 最近一天（%s）失败明细 ===" % days[-1])
    per = dm[days[-1]]
    bad = [(n, x) for n, x in sorted(per.items()) if x["error"] > 0]
    if not bad:
        print("（无失败）")
    for n, x in bad:
        print("  %-22s 跑 %d 次 / 成功 %d / 失败 %d" % (n[:22], x["done"] + x["error"], x["done"], x["error"]))
        if x["err"]:
            print("      error_info: %s" % x["err"])

    print("\n=== 失败原因归类（%s ~ %s，按运行次数）===" % (days[0], days[-1]))
    from collections import Counter
    c: Counter = Counter()
    acct_of: dict[str, set] = {}
    for d in days:
        for n, x in dm[d].items():
            for _t, reason in (x["eid"] or {}).items():
                c[reason] += 1
                acct_of.setdefault(reason, set()).add(n)
    for k, n in c.most_common(12):
        print("  %4d 次 / %2d 个账号: %s" % (n, len(acct_of.get(k, ())), k))
    if not c:
        print("（无）")

    print("\n=== 连续失败（含 ERROR 的天数，从最近一天往回数）===")
    streak = []
    for n in sorted(reg):
        k = 0
        for d in reversed(days):
            x = dm[d].get(n)
            if x and x["error"] > 0:
                k += 1
            elif x is None and k == 0:
                continue          # 今天还没跑：不算断，也不算连
            else:
                break
        if k >= 2:
            streak.append((k, n))
    for k, n in sorted(streak, reverse=True):
        print("  %-22s 连续 %d 天有失败" % (n[:22], k))
    if not streak:
        print("（无连续 ≥2 天）")

    if a.account:
        print("\n=== %s 逐日轨迹 ===" % a.account)
        for d in days:
            x = dm[d].get(a.account)
            if not x:
                print("  %s  无记录" % d)
                continue
            runs = " ".join("%s:%s" % (t, s) for t, s in x["runs"])
            print("  %s  跑 %d / 成 %d / 败 %d  [%s]" % (d, x["done"] + x["error"], x["done"], x["error"], runs))
            if x["err"]:
                print("        error_info: %s" % x["err"])

    print("\n=== 最近一天没跑的账号 ===")
    idle = [n for n in sorted(reg) if n not in dm[days[-1]]]
    print(" 共 %d 个: %s" % (len(idle), "、".join(x[:16] for x in idle[:20]) or "（无）"))


if __name__ == "__main__":
    main()
