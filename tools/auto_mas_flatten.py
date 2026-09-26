# -*- coding: utf-8 -*-
"""AUTO-MAS 队列扁平化：把"特别长"的队列里的脚本项，匀给早收工的队列。

负载模型：一个脚本项 ≈ 单项时长 × 启用账号数（同一脚本的多个账号是串行跑的）。
目标：8 条队列的预计总时长尽量相等（min-max）。

关键结构（2026-09-20 查源码 + 实配确认）：
1. `app/core/task_manager.py` 的 `Task.prepare()` 在任务启动时把 QueueItem 拍成
   `task_info.script_list` 快照 ⇒ **改队列配置不会打断在飞的队列**，下一次发车才生效。
2. 每条队列槽位独占一个 MAA 运行目录（E:\\MAA-Multi\\MAA_<槽位号>），同槽位所有脚本项
   都指向它 ⇒ 搬项必须把脚本 Info.Path 改绑到目标槽位目录。
3. **日常定时N 与 晚班定时N 共用同一个槽位**（脚本只有一个 Path）⇒ 两套队列必须用
   同一份"脚本→槽位"分配，不能各算各的，否则同一 MAA 目录会被两条队列同时抢。
4. **同一个模拟器（Emulator.Index）的脚本项必须待在同一个槽位**——不同槽位是并行跑的，
   同实例分到两条队列会互相抢设备。所以搬动的单位是"同 emu 的一组项"，不是单个项。

用法：
    python tools/auto_mas_flatten.py                  # dry-run（只打印计划）
    python tools/auto_mas_flatten.py --apply          # 实配（日常+晚班两套一起）
    python tools/auto_mas_flatten.py --slot-minutes 37
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict

API = "http://100.79.173.69:36163"
PREFIX = {"daily": "日常定时", "evening": "晚班定时"}


def api(path: str, body: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body if body is not None else {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load_scripts() -> dict:
    sc = api("/api/scripts/get", {"scriptId": None})
    out = {}
    for sid, sv in (sc.get("data") or {}).items():
        info = sv.get("Info") or {}
        # /api/scripts/get 不返回用户列表，要单独问 user/get（data = {userUid: {Info}}）
        ud = api("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {}
        users = [uv.get("Info") or {} for k, uv in ud.items()
                 if k != "instances" and isinstance(uv, dict)]
        out[sid] = {
            "name": info.get("Name", "?"),
            "path": (info.get("Path") or "").replace("/", "\\"),
            "emu": str((sv.get("Emulator") or {}).get("Index", "")),
            "enabled": [u.get("Name", "?") for u in users if u.get("Status")],
        }
    return out


def load_slots(scripts: dict) -> dict:
    """{set_key: {槽位号: {"qid":…, "name":…, "items":[{uid,sid,emu}]}}}"""
    qs = api("/api/queue/get", {"queueId": None})
    slots = defaultdict(dict)
    for qid, qv in (qs.get("data") or {}).items():
        name = (qv.get("Info") or {}).get("Name", "?")
        for key, prefix in PREFIX.items():
            m = re.fullmatch(re.escape(prefix) + r"(\d+)", name)
            if not m:
                continue
            r = api("/api/queue/item/get", {"queueId": qid, "queueItemId": None})
            data = r.get("data") or {}
            items = []
            for it in r.get("index") or []:
                sid = ((data.get(it["uid"]) or {}).get("Info") or {}).get("ScriptId")
                items.append({"uid": it["uid"], "sid": sid,
                              "emu": scripts.get(sid, {}).get("emu")})
            slots[key][int(m.group(1))] = {"qid": qid, "name": name, "items": items}
    return slots


def units_of(items: list, w: dict, scripts: dict) -> list:
    """按 emu 归组：同模拟器的项是一个不可拆的搬动单位。"""
    g = defaultdict(list)
    for it in items:
        g[it["emu"]].append(it)
    out = []
    for emu, its in g.items():
        sids = [i["sid"] for i in its]
        out.append({"emu": emu, "items": its, "sids": sids,
                    "w": sum(w.get(s, 0) for s in sids),
                    "names": [scripts.get(s, {}).get("name", "?") for s in sids]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实配（默认 dry-run）")
    ap.add_argument("--slot-minutes", type=float, default=37.0, help="每个账号的基准分钟")
    args = ap.parse_args()

    scripts = load_scripts()
    slots = load_slots(scripts)
    if not slots["daily"] or not slots["evening"]:
        sys.exit("找不到 日常定时/晚班定时 队列")
    nos = sorted(slots["daily"])
    w = {sid: (max(1, len(sv["enabled"])) if sv["enabled"] else 0) for sid, sv in scripts.items()}

    load, units = {}, {}
    for no in nos:
        units[no] = units_of(slots["daily"][no]["items"], w, scripts)
        load[no] = sum(u["w"] for u in units[no])
    bad = [no for no in nos
           if {i["sid"] for i in slots["daily"][no]["items"]}
           != {i["sid"] for i in slots["evening"][no]["items"]}]
    if bad:
        print(f"[警告] 槽位 {bad} 的日常/晚班脚本集合不一致，仍按日常那套算")

    path_of = {}
    for no in nos:
        c = Counter(scripts[i["sid"]]["path"] for i in slots["daily"][no]["items"]
                    if i["sid"] in scripts)
        # 空槽位（刚扩出来的）取不到 Path ⇒ 按命名约定推 MAA_<槽位号>，
        # 绝不能留空：留空会把搬进去的脚本 Path 改绑成 "" 直接废掉它。
        path_of[no] = c.most_common(1)[0][0] if c else "E:\\MAA-Multi\\MAA_%d" % no
        if len(c) > 1:
            print(f"[警告] {slots['daily'][no]['name']} 内出现多个 MAA 目录: {dict(c)}")

    total, n = sum(load.values()), len(nos)
    print(f"启用账号 {total} 个 → {n} 条队列，理论均值 {total / n:.1f}")
    for no in sorted(nos, key=lambda x: -load[x]):
        emus = "、".join(sorted({u["emu"] for u in units[no] if u["w"]}))
        print(f"  {slots['daily'][no]['name']:10s} 负载 {load[no]:2d} "
              f"项 {len(slots['daily'][no]['items'])}  {path_of[no][-6:]}  "
              f"≈{load[no] * args.slot_minutes / 60:.1f} 小时  (emu: {emus})")

    moves = []
    while True:
        heavy = max(nos, key=lambda q: load[q])
        light = min(nos, key=lambda q: load[q])
        if load[heavy] - load[light] < 2:
            break
        cand = [u for u in units[heavy] if u["w"] > 0]
        if not cand:
            break
        u = min(cand, key=lambda x: (x["w"], x["names"][0]))
        if load[heavy] - u["w"] < load[light] + u["w"] - 1:
            break
        moves.append((u, heavy, light))
        units[heavy].remove(u)
        units[light].append(u)
        load[heavy] -= u["w"]
        load[light] += u["w"]

    if not moves:
        print("\n⇒ 各队列已经很平，无需搬动")
        return

    print("\n⇒ 计划搬动（日常/晚班两套同步）:")
    for u, a, b in moves:
        print(f"   {slots['daily'][a]['name']} → {slots['daily'][b]['name']}"
              f"  账号{u['w']} emu{u['emu']}: {'、'.join(u['names'])}")
    print(f"⇒ 搬后负载 {sorted((load[no] for no in nos), reverse=True)}"
          f"  最长队 ≈{max(load.values()) * args.slot_minutes / 60:.1f} 小时")

    if not args.apply:
        print("\n[dry-run] 未改动。加 --apply 生效（只影响下一次发车，不打断在飞的队列）。")
        return

    for u, a, b in moves:
        tgt = path_of[b]
        for sid in u["sids"]:
            if scripts[sid]["path"] != tgt:
                r = api("/api/scripts/update", {"scriptId": sid, "data": {"Info": {"Path": tgt}}})
                assert r.get("code") == 200, f"改 Path 失败 {scripts[sid]['name']}: {r}"
                print(f"  Path {scripts[sid]['name']}: {scripts[sid]['path']} → {tgt}")
        for key in PREFIX:
            src, dst = slots[key][a], slots[key][b]
            for sid in u["sids"]:
                it = next((x for x in src["items"] if x["sid"] == sid), None)
                if not it:
                    print(f"  [跳过] {key} {src['name']} 里没有 {scripts[sid]['name']}")
                    continue
                r = api("/api/queue/item/delete", {"queueId": src["qid"], "queueItemId": it["uid"]})
                assert r.get("code") == 200, f"item/delete 失败: {r}"
                r1 = api("/api/queue/item/add", {"queueId": dst["qid"]})
                iid = r1.get("queueItemId") or (r1.get("data") or {}).get("uid")
                assert iid, f"item/add 未返回 id: {r1}"
                r2 = api("/api/queue/item/update", {"queueId": dst["qid"], "queueItemId": iid,
                                                    "data": {"Info": {"ScriptId": sid}}})
                assert r2.get("code") == 200, f"item/update 失败: {r2}"
                print(f"  {src['name']} → {dst['name']}: {scripts[sid]['name']} ✓")
    print("\n完成。可再跑一次 dry-run 复核。")


if __name__ == "__main__":
    sys.exit(main())
