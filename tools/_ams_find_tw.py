# -*- coding: utf-8 -*-
"""临时：列出 AUTO-MAS 线上所有用户，按服务器分组，重点看 txwy。"""
import importlib.util
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = importlib.util.spec_from_file_location(
    "tg", Path(__file__).resolve().parent / "auto_mas_toggle_user.py")
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)

scripts = tg.api("/api/scripts/get", {"scriptId": None})["data"]
rows = []
for sid, sv in scripts.items():
    d = (tg.api("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {})
    for uid, u in d.items():
        i = u.get("Info", {})
        rows.append({
            "sid": sid,
            "script": sv["Info"].get("Name"),
            "maa": sv["Info"].get("Path"),
            "emu": sv["Emulator"].get("Index"),
            "uid": uid,
            "name": i.get("Name"),
            "server": i.get("Server"),
            "status": i.get("Status"),
            "stage": i.get("Stage"),
            "stages": [i.get("Stage_1"), i.get("Stage_2"), i.get("Stage_3")],
            "mode": i.get("StageMode"),
            "anni": i.get("Annihilation"),
            "infrast": i.get("InfrastMode"),
            "notes": i.get("Notes"),
        })

by = {}
for r in rows:
    by.setdefault(r["server"], []).append(r)
print("用户总数:", len(rows), "| 按服务器:", {k: len(v) for k, v in by.items()})

print("\n=== txwy / 非三大服 的用户 ===")
for r in rows:
    if r["server"] not in ("Official", "Bilibili", "YoStarJP"):
        print(json.dumps(r, ensure_ascii=False, indent=2))

print("\n=== 该用户所属脚本的完整 Task 开关 ===")
for r in rows:
    if r["server"] not in ("Official", "Bilibili", "YoStarJP"):
        u = tg.api("/api/scripts/user/get", {"scriptId": r["sid"], "userId": r["uid"]})["data"][r["uid"]]
        print("Task:", json.dumps(u.get("Task"), ensure_ascii=False)[:1500])
        print("Data:", json.dumps(u.get("Data"), ensure_ascii=False)[:300])
